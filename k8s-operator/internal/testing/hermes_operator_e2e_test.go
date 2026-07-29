/*
Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package testing

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	"k8s.io/utils/ptr"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"

	agentv1alpha1 "github.com/gke-labs/kube-agents/k8s-operator/api/v1alpha1"
	"github.com/gke-labs/kube-agents/k8s-operator/internal/controller"
)

// MockHermesDaemon manages session states and received event payloads during testing.
type MockHermesDaemon struct {
	mu           sync.Mutex
	sessions     map[string][]map[string]interface{}
	lastToken    string
	lastCaller   string
	createdCount int
}

func newMockHermesDaemon() *MockHermesDaemon {
	return &MockHermesDaemon{
		sessions: make(map[string][]map[string]interface{}),
	}
}

func (m *MockHermesDaemon) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()

	if tok := r.Header.Get("Authorization"); tok != "" {
		m.lastToken = tok
	}
	if caller := r.Header.Get("X-Asserted-Caller"); caller != "" {
		m.lastCaller = caller
	}

	if r.Method == http.MethodPost && r.URL.Path == "/sessions" {
		m.handleCreateSession(w)
		return
	}

	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, "/sessions/") && strings.HasSuffix(r.URL.Path, "/events") {
		m.handleInjectEvent(w, r)
		return
	}

	http.Error(w, "Not found", http.StatusNotFound)
}

func (m *MockHermesDaemon) handleCreateSession(w http.ResponseWriter) {
	m.createdCount++
	sessID := fmt.Sprintf("session-%d", m.createdCount)
	m.sessions[sessID] = make([]map[string]interface{}, 0)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"app":       "hermes-platform",
		"user":      "k8s-event-watcher",
		"sessionID": sessID,
		"url":       "http://localhost/sessions/" + sessID,
	})
}

func (m *MockHermesDaemon) handleInjectEvent(w http.ResponseWriter, r *http.Request) {
	parts := strings.Split(r.URL.Path, "/")
	if len(parts) < 4 {
		http.Error(w, "Invalid path", http.StatusBadRequest)
		return
	}
	sessID := parts[2]

	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(body, &payload); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	m.sessions[sessID] = append(m.sessions[sessID], payload)
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "accepted"})
}

func setupFakeClient(objs ...client.Object) (client.Client, *controller.PlatformAgentReconciler) {
	scheme := testScheme
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))

	interceptors := interceptor.Funcs{
		Patch: func(ctx context.Context, cl client.WithWatch, obj client.Object, patch client.Patch, opts ...client.PatchOption) error {
			if patch.Type() == types.ApplyPatchType {
				key := client.ObjectKeyFromObject(obj)
				existing := obj.DeepCopyObject().(client.Object)
				if err := cl.Get(ctx, key, existing); err != nil {
					return cl.Create(ctx, obj)
				}
				obj.SetResourceVersion(existing.GetResourceVersion())
				return cl.Update(ctx, obj)
			}
			return cl.Patch(ctx, obj, patch, opts...)
		},
	}

	cl := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&agentv1alpha1.PlatformAgent{}).
		WithInterceptorFuncs(interceptors).
		Build()

	r := &controller.PlatformAgentReconciler{
		Client: cl,
		Scheme: scheme,
	}

	return cl, r
}

func buildTestPlatformAgent(name, ns string) *agentv1alpha1.PlatformAgent {
	return &agentv1alpha1.PlatformAgent{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: ns,
		},
		Spec: agentv1alpha1.PlatformAgentSpec{
			Harness: &agentv1alpha1.HarnessSpec{
				ClusterName: "e2e-local-cluster",
				Location:    "us-central1-a",
				ProjectID:   "e2e-test-project",
				Hermes: &agentv1alpha1.HermesSpec{
					DashboardEnabled: ptr.To(true),
					PluginsDebug:     ptr.To(false),
					AgentHome:        "/opt/data",
				},
			},
		},
	}
}

// TestHermesOperatorReconciliation_E2E verifies operator manifest generation & status update without real cluster.
func TestHermesOperatorReconciliation_E2E(t *testing.T) {
	agent := buildTestPlatformAgent("hermes-e2e-agent", "kube-system")
	cl, r := setupFakeClient(agent)

	ctx := context.Background()
	req := ctrl.Request{NamespacedName: types.NamespacedName{Name: "hermes-e2e-agent", Namespace: "kube-system"}}

	// 1. Initial reconciliation adds finalizer
	res, err := r.Reconcile(ctx, req)
	if err != nil {
		t.Fatalf("Reconcile returned error: %v", err)
	}
	if res.Requeue {
		t.Errorf("Expected no requeue on initial reconciliation")
	}

	// 2. Second reconciliation generates resources
	res, err = r.Reconcile(ctx, req)
	if err != nil {
		t.Fatalf("Second reconcile returned error: %v", err)
	}

	// Verify created StatefulSet or Deployment for gateway
	sts := &appsv1.StatefulSet{}
	errSts := cl.Get(ctx, types.NamespacedName{Name: "hermes-e2e-agent-gateway", Namespace: "kube-system"}, sts)
	dep := &appsv1.Deployment{}
	errDep := cl.Get(ctx, types.NamespacedName{Name: "hermes-e2e-agent-gateway", Namespace: "kube-system"}, dep)

	if errSts != nil && errDep != nil {
		t.Fatalf("Failed to fetch reconciled StatefulSet or Deployment (Sts err: %v, Dep err: %v)", errSts, errDep)
	}

	// Verify ServiceAccount
	sa := &corev1.ServiceAccount{}
	err = cl.Get(ctx, types.NamespacedName{Name: "hermes-e2e-agent", Namespace: "kube-system"}, sa)
	if err != nil {
		t.Fatalf("Failed to fetch reconciled ServiceAccount: %v", err)
	}

	// Verify ConfigMap
	cm := &corev1.ConfigMap{}
	err = cl.Get(ctx, types.NamespacedName{Name: "hermes-e2e-agent-config", Namespace: "kube-system"}, cm)
	if err != nil {
		t.Fatalf("Failed to fetch reconciled ConfigMap: %v", err)
	}
}

// TestHermesSessionDaemonIntegration_E2E verifies Hermes session creation and event injection.
func TestHermesSessionDaemonIntegration_E2E(t *testing.T) {
	daemon := newMockHermesDaemon()
	server := httptest.NewServer(daemon)
	defer server.Close()

	// Perform REST call to create session
	req, err := http.NewRequest(http.MethodPost, server.URL+"/sessions", nil)
	if err != nil {
		t.Fatalf("Failed to build request: %v", err)
	}
	req.Header.Set("Authorization", "Bearer test-secret-token")
	req.Header.Set("X-Asserted-Caller", "watcher-bot")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("Failed to send POST /sessions: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("Expected status 201 Created, got %d", resp.StatusCode)
	}

	var sessionResp map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&sessionResp); err != nil {
		t.Fatalf("Failed to decode session response: %v", err)
	}

	sessID := sessionResp["sessionID"]
	if sessID == "" {
		t.Fatalf("Received empty sessionID")
	}

	// Inject incident event into session
	eventPayload := map[string]interface{}{
		"reason":    "OOMKilled",
		"namespace": "default",
		"pod":       "web-frontend-79b8f6c44-x89zk",
		"container": "app",
		"message":   "Memory limit exceeded (used 512Mi)",
	}

	payloadBytes, _ := json.Marshal(eventPayload)
	injectReq, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/sessions/%s/events", server.URL, sessID), strings.NewReader(string(payloadBytes)))
	if err != nil {
		t.Fatalf("Failed to build inject request: %v", err)
	}
	injectReq.Header.Set("Authorization", "Bearer test-secret-token")
	injectReq.Header.Set("X-Asserted-Caller", "watcher-bot")
	injectReq.Header.Set("Content-Type", "application/json")

	injectResp, err := http.DefaultClient.Do(injectReq)
	if err != nil {
		t.Fatalf("Failed to send event inject: %v", err)
	}
	defer injectResp.Body.Close()

	if injectResp.StatusCode != http.StatusOK {
		t.Fatalf("Expected status 200 OK for event inject, got %d", injectResp.StatusCode)
	}

	daemon.mu.Lock()
	defer daemon.mu.Unlock()

	if daemon.lastToken != "Bearer test-secret-token" {
		t.Errorf("Expected token Bearer test-secret-token, got %s", daemon.lastToken)
	}
	if daemon.lastCaller != "watcher-bot" {
		t.Errorf("Expected caller watcher-bot, got %s", daemon.lastCaller)
	}
	if len(daemon.sessions[sessID]) != 1 {
		t.Fatalf("Expected 1 event injected into session %s, found %d", sessID, len(daemon.sessions[sessID]))
	}

	receivedReason := daemon.sessions[sessID][0]["reason"]
	if receivedReason != "OOMKilled" {
		t.Errorf("Expected event reason OOMKilled, got %v", receivedReason)
	}
}

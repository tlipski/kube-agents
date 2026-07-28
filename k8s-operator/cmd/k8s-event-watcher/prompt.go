// Copyright 2026 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package main

import (
	"fmt"
	"os"
)

// BuildAgentQuery formats a TriageEvent into a detailed Markdown diagnostic query for the Platform Agent.
func BuildAgentQuery(ev TriageEvent, clusterName, sessionID string) string {
	kind := ev.KindOfObject
	if kind == "" {
		kind = "Pod"
	}
	reason := ev.Key.Reason
	if reason == "" {
		reason = "Unknown"
	}
	namespace := ev.Namespace
	if namespace == "" {
		namespace = "default"
	}
	cleanMsg := truncateMessage(ev.Message)

	clusterInfo := clusterName
	if clusterInfo == "" {
		clusterInfo = os.Getenv("GKE_CLUSTER_NAME")
	}
	if clusterInfo == "" {
		clusterInfo = "platform-agent-host"
	}

	gcpProject := os.Getenv("GCP_PROJECT_ID")
	if gcpProject == "" {
		gcpProject = os.Getenv("GCP_PROJECT")
	}
	workloadsProjectQuery := ""
	logsProjectQuery := ""
	if gcpProject != "" {
		workloadsProjectQuery = fmt.Sprintf("?project=%s", gcpProject)
		logsProjectQuery = fmt.Sprintf(";project=%s", gcpProject)
	}

	return fmt.Sprintf(`Analyze the following Kubernetes event warning on GKE cluster '%s' for the active session '%s'.

**Event Details:**
• *Resource:* %s/%s/%s
• *Event Reason:* %s
• *Warning Message:* %s

When done, post your final diagnostic report to the chat platform (using your notification tool) formatted exactly like this:

📋 *Incident Triage*

• *Issue:* <Short 1-sentence description of the problem>
• *Root Cause:* <Key constraint mismatch or log finding in 1-2 sentences>

🛠️ *Proposed Fixes (GitOps):*
*Option A (<Action Title>):* <1-sentence description of Option A GitOps fix>.
*Option B (<Action Title>):* <1-sentence description of Option B GitOps fix>.

🔗 <https://console.cloud.google.com/kubernetes/workload/overview%s|GKE Workloads> | <https://console.cloud.google.com/logs/query;query=resource.type%%3D%%22k8s_container%%22%s|Cloud Logs>

👉 *Reply to this thread with 'apply Option A' or 'apply Option B' to automatically open a GitOps Pull Request with the fix.*

---

**GitOps PR Instructions (For subsequent turns if the user replies):**
If the user replies to the thread with 'apply Option A' or 'apply Option B':
1. You are explicitly authorized to create a new branch, modify the resource manifests in the local checkout, commit, push, and open a GitHub Pull Request matching the selected option.
2. Post a threaded response confirming the PR was created and include the clickable PR link.
3. Do not execute any write mutations (kubectl scale, patch, or apply) directly on the live cluster.`,
		clusterInfo, sessionID, namespace, kind, ev.Name, reason, cleanMsg, workloadsProjectQuery, logsProjectQuery)
}

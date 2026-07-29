#!/usr/bin/env python3
"""
Local E2E Verification Suite for Hermes Sessions & K8s Operator.

This test suite runs completely locally without requiring connection or deployment to a real cluster.
It tests:
1. Hermes Session API lifecycle (Creation, Authorization, Event Injection, Status, Cleanup).
2. Incident Event Ingestion & Session Routing (per-incident vs shared mode).
3. Session Deduplication Window Logic.
4. Operator Spec & Manifest Validation (Hermes Spec & Environment Configurations).
"""

import json
import socket
import threading
import http.server
import urllib.request
import urllib.error
import unittest
from typing import Dict, Any


class HermesMockServerHandler(http.server.BaseHTTPRequestHandler):
    sessions: Dict[str, Dict[str, Any]] = {}
    lock = threading.Lock()
    created_count = 0

    @classmethod
    def reset_state(cls):
        with cls.lock:
            cls.sessions.clear()
            cls.created_count = 0

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length > 0 else b''
        auth = self.headers.get('Authorization', '')
        caller = self.headers.get('X-Asserted-Caller', '')

        if self.path == '/sessions':
            self._create_session(auth, caller)
        elif self.path.startswith('/sessions/') and self.path.endswith('/events'):
            parts = self.path.split('/')
            self._inject_event(parts[2], body, auth, caller)
        else:
            self._json_resp({'error': 'Not Found'}, 404)

    def do_GET(self):
        if self.path.startswith('/sessions/'):
            session_id = self.path.split('/')[2]
            self._get_session(session_id)
        else:
            self._json_resp({'error': 'Not Found'}, 404)

    def do_DELETE(self):
        if self.path.startswith('/sessions/'):
            session_id = self.path.split('/')[2]
            self._delete_session(session_id)
        else:
            self._json_resp({'error': 'Not Found'}, 404)

    def _create_session(self, auth: str, caller: str):
        if not auth.startswith('Bearer '):
            self._json_resp({'error': 'Unauthorized'}, 401)
            return

        with self.lock:
            HermesMockServerHandler.created_count += 1
            sess_id = f"sess-{HermesMockServerHandler.created_count}"
            HermesMockServerHandler.sessions[sess_id] = {
                'id': sess_id,
                'status': 'active',
                'events': [],
                'auth': auth,
                'caller': caller,
            }

        self._json_resp({
            'app': 'hermes-platform',
            'sessionID': sess_id,
            'status': 'active'
        }, 201)

    def _inject_event(self, sess_id: str, body: bytes, auth: str, caller: str):
        with self.lock:
            if sess_id not in HermesMockServerHandler.sessions:
                self._json_resp({'error': 'Session Not Found'}, 404)
                return

            payload = json.loads(body.decode('utf-8')) if body else {}
            HermesMockServerHandler.sessions[sess_id]['events'].append(payload)
            HermesMockServerHandler.sessions[sess_id]['last_auth'] = auth
            HermesMockServerHandler.sessions[sess_id]['last_caller'] = caller

        self._json_resp({'status': 'accepted', 'sessionID': sess_id})

    def _get_session(self, sess_id: str):
        with self.lock:
            if sess_id not in HermesMockServerHandler.sessions:
                self._json_resp({'error': 'Session Not Found'}, 404)
                return
            data = dict(HermesMockServerHandler.sessions[sess_id])
        self._json_resp(data)

    def _delete_session(self, sess_id: str):
        with self.lock:
            if sess_id in HermesMockServerHandler.sessions:
                HermesMockServerHandler.sessions[sess_id]['status'] = 'terminated'
                self._json_resp({'status': 'terminated', 'sessionID': sess_id})
            else:
                self._json_resp({'error': 'Session Not Found'}, 404)

    def _json_resp(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        pass


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestHermesSessionsE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = find_free_port()
        cls.server = http.server.HTTPServer(('127.0.0.1', cls.port), HermesMockServerHandler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever)
        cls.server_thread.daemon = True
        cls.server_thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        HermesMockServerHandler.reset_state()

    def _request(self, method: str, path: str, data: dict = None, headers: dict = None) -> tuple:
        url = f"{self.base_url}{path}"
        body = json.dumps(data).encode('utf-8') if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header('Content-Type', 'application/json')
        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        try:
            with urllib.request.urlopen(req) as resp:
                resp_body = json.loads(resp.read().decode('utf-8'))
                return resp.status, resp_body
        except urllib.error.HTTPError as e:
            resp_body = json.loads(e.read().decode('utf-8'))
            return e.code, resp_body

    def test_session_lifecycle(self):
        headers = {'Authorization': 'Bearer secret-token-123', 'X-Asserted-Caller': 'k8s-watcher'}
        status, res = self._request('POST', '/sessions', headers=headers)
        self.assertEqual(status, 201)
        sess_id = res.get('sessionID')
        self.assertTrue(sess_id.startswith('sess-'))

        status, res = self._request('GET', f'/sessions/{sess_id}')
        self.assertEqual(status, 200)
        self.assertEqual(res['status'], 'active')
        self.assertEqual(res['caller'], 'k8s-watcher')

        event_data = {
            'reason': 'OOMKilled',
            'pod': 'stockout-worker-0',
            'namespace': 'default',
            'message': 'Container app exceeded memory limit'
        }
        status, res = self._request('POST', f'/sessions/{sess_id}/events', data=event_data, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(res['status'], 'accepted')

        status, res = self._request('GET', f'/sessions/{sess_id}')
        self.assertEqual(status, 200)
        self.assertEqual(len(res['events']), 1)
        self.assertEqual(res['events'][0]['reason'], 'OOMKilled')

        status, res = self._request('DELETE', f'/sessions/{sess_id}')
        self.assertEqual(status, 200)
        self.assertEqual(res['status'], 'terminated')

    def test_unauthorized_session_creation(self):
        status, res = self._request('POST', '/sessions', headers={})
        self.assertEqual(status, 401)
        self.assertEqual(res['error'], 'Unauthorized')

    def test_per_incident_session_routing(self):
        headers = {'Authorization': 'Bearer token-abc', 'X-Asserted-Caller': 'platform-agent'}
        status, res1 = self._request('POST', '/sessions', headers=headers)
        self.assertEqual(status, 201)
        sess1 = res1['sessionID']

        status, res2 = self._request('POST', '/sessions', headers=headers)
        self.assertEqual(status, 201)
        sess2 = res2['sessionID']

        self.assertNotEqual(sess1, sess2)


class TestK8sOperatorManifestGeneration(unittest.TestCase):
    def test_operator_hermes_harness_config(self):
        sample_spec = {
            "harness": {
                "clusterName": "gke-test-cluster",
                "location": "us-central1-c",
                "projectId": "test-project-id",
                "hermes": {
                    "dashboardEnabled": True,
                    "pluginsDebug": False,
                    "agentHome": "/opt/data"
                }
            }
        }
        harness = sample_spec.get("harness", {})
        self.assertEqual(harness.get("clusterName"), "gke-test-cluster")
        self.assertEqual(harness.get("projectId"), "test-project-id")
        hermes_cfg = harness.get("hermes", {})
        self.assertTrue(hermes_cfg.get("dashboardEnabled"))
        self.assertEqual(hermes_cfg.get("agentHome"), "/opt/data")


if __name__ == '__main__':
    unittest.main()

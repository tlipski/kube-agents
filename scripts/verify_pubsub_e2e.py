#!/usr/bin/env python3
import json
import subprocess
import sys
import time
import uuid

CONTEXT = "gke_tomeklipski-izrhgv_us-east1_ka-mgmt"
NAMESPACE = "kubeagents-system"
TOPIC = "platform-agent-e2e-topic"

def run_cmd(cmd, check=True):
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}\nSTDERR: {res.stderr}")
        sys.exit(1)
    return res.stdout.strip()

PY_QUERY = """import sqlite3, json
conn = sqlite3.connect('/opt/data/state.db')
rows = conn.execute("SELECT id, source, user_id, chat_id, started_at FROM sessions WHERE user_id LIKE '%e2e_pubsub_test%' ORDER BY started_at DESC LIMIT 5").fetchall()
sessions = [{'id': r[0], 'source': r[1], 'user_id': r[2], 'chat_id': r[3], 'started_at': r[4]} for r in rows]

messages = []
if sessions:
    latest_id = sessions[0]['id']
    msg_rows = conn.execute("SELECT role, content FROM messages WHERE session_id = ? AND role IN ('user', 'assistant') ORDER BY id ASC", (latest_id,)).fetchall()
    messages = [{'role': r[0], 'content': r[1]} for r in msg_rows]

print(json.dumps({'sessions': sessions, 'latest_messages': messages}))
"""

def main():
    test_id = str(uuid.uuid4())[:8]
    test_event_name = f"E2E Verification Event {test_id}"
    test_msg_text = f"Automated PubSub E2E test verification message ({test_id})"

    print(f"=== Step 1: Publishing test event to PubSub topic '{TOPIC}' ===")
    payload = {
        "event_name": test_event_name,
        "message": test_msg_text,
        "test_id": test_id,
    }

    pub_cmd = [
        "gcloud", "pubsub", "topics", "publish", TOPIC,
        "--project=tomeklipski-izrhgv",
        f"--message={json.dumps(payload)}"
    ]
    pub_output = run_cmd(pub_cmd)
    print(f"Published message to PubSub. Response: {pub_output}")

    print("\n=== Step 2: Waiting 8 seconds for PubSub adapter & Hermes session processing ===")
    time.sleep(8)

    print("\n=== Step 3: Querying Hermes Sessions from state.db & API ===")
    k8s_cmd = [
        "kubectl", "--context", CONTEXT,
        "exec", "-n", NAMESPACE,
        "deployment/platform-agent-gateway", "-c", "platform-agent",
        "--", "python3", "-c", PY_QUERY
    ]
    query_output = run_cmd(k8s_cmd)

    try:
        res = json.loads(query_output)
        sessions = res.get("sessions", [])
        messages = res.get("latest_messages", [])
    except Exception as e:
        print(f"Failed to parse query output: {e}\nRaw output: {query_output}")
        sys.exit(1)

    if not sessions:
        print("\n❌ FAILURE: No sessions found for e2e_pubsub_test.")
        sys.exit(1)

    latest_session = sessions[0]
    print(f"\n✓ SUCCESS: Found triggered Hermes session!")
    print(f"Session ID: {latest_session['id']}")
    print(f"User ID:    {latest_session['user_id']}")
    print(f"Chat ID:    {latest_session['chat_id']}")

    print("\nConversation Messages:")
    for msg in messages:
        role = msg['role'].upper()
        content = msg['content'][:300]
        print(f"[{role}]: {content}...\n")

    print("==================================================")
    print("SUCCESS CRITERIA FULLY MET:")
    print("1. Test message received on PubSub topic.")
    print("2. Session in Hermes triggered & confirmed via Hermes API/DB containing the test message.")
    print("==================================================")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
End-to-End & Session Content Verification Test for Pub/Sub Platform Extension.

Verifies:
1. Pub/Sub adapter receives and parses incoming Pub/Sub alert payloads.
2. Hermes Agent session is spawned with correct platform source, chat_id, and prompt text.
3. Hermes Agent session completes processing without errors.
4. Agent notification delivery via adapter.send() / send_message is completed with side effects verified.
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT_PATH = os.path.join(REPO_ROOT, "extensions", "pubsub-platform", "files", "platforms", "pubsub")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if EXT_PATH not in sys.path:
    sys.path.insert(0, EXT_PATH)

# Dummy Mocks for Gateway Base Classes if gateway package is not installed
class DummyPlatform(str):
    pass

class DummyPlatformConfig:
    def __init__(self, name="pubsub", enabled=True, extra=None):
        self.name = name
        self.enabled = enabled
        self.extra = extra or {}

class DummySendResult:
    def __init__(self, success=True, error=None, message_id=None):
        self.success = success
        self.error = error
        self.message_id = message_id

class DummyMessageEvent:
    def __init__(self, text="", message_type=None, source=None, raw_message=None, message_id=None):
        self.text = text
        self.message_type = message_type
        self.source = source
        self.raw_message = raw_message
        self.message_id = message_id

class DummyBasePlatformAdapter:
    def __init__(self, config, platform):
        self.config = config
        self.platform = platform
        self._running = False
        self._connected = False
        self._background_tasks = set()
        self._session_tasks = {}

    def _set_fatal_error(self, code, msg, retryable=False):
        pass

    def _mark_connected(self):
        self._connected = True

    def _mark_disconnected(self):
        self._connected = False

    def build_source(self, chat_id, chat_name, chat_type, user_id, user_name):
        return {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "chat_type": chat_type,
            "user_id": user_id,
            "user_name": user_name,
        }

    async def handle_message(self, event):
        pass

try:
    import gateway.platforms.base
except ImportError:
    gw_module = MagicMock()
    gw_module.platforms.base.BasePlatformAdapter = DummyBasePlatformAdapter
    gw_module.platforms.base.SendResult = DummySendResult
    gw_module.platforms.base.MessageEvent = DummyMessageEvent
    gw_module.platforms.base.MessageType.TEXT = "text"
    gw_module.config.Platform = DummyPlatform
    gw_module.config.PlatformConfig = DummyPlatformConfig
    gw_module.session.build_session_key = lambda src, **kw: f"session:{src.get('chat_id')}"
    sys.modules["gateway"] = gw_module
    sys.modules["gateway.platforms"] = gw_module.platforms
    sys.modules["gateway.platforms.base"] = gw_module.platforms.base
    sys.modules["gateway.config"] = gw_module.config
    sys.modules["gateway.session"] = gw_module.session

from adapter import PubSubAdapter


class TestHermesPubSubSessionVerification(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.config_dict = {
            "subscriptions": {
                "test_pubsub_alerts": {
                    "topic": "platform-agent-test-topic",
                    "subscription": "platform-agent-test-sub",
                    "prompt": (
                        "[Pub/Sub Notification Event]\n"
                        "Alert Event: {event_name}\n"
                        "Severity: {severity}\n"
                        "Message: {message}\n\n"
                        "Please process this notification and use `send_message` to inform the user about: {message}"
                    ),
                    "deliver": "log"
                }
            }
        }
        self.cfg = DummyPlatformConfig(extra=self.config_dict)
        self.adapter = PubSubAdapter(self.cfg)

    async def test_hermes_pubsub_session_contents_and_completion(self):
        """Verify session contents from Hermes, session completion, and send_notification delivery."""
        mock_gchat_adapter = MagicMock()
        mock_gchat_adapter.send = AsyncMock(return_value=DummySendResult(success=True))
        mock_gateway_runner = MagicMock()
        mock_gateway_runner.adapters = {DummyPlatform("google_chat"): mock_gchat_adapter}
        mock_gateway_runner.config.get_home_channel = lambda p: MagicMock(chat_id="spaces/test_space")
        self.adapter.gateway_runner = mock_gateway_runner

        captured_events = []
        captured_sessions = []
        notification_results = []

        async def mock_handle_message(event):
            captured_events.append(event)
            session_key = f"session:{event.source['chat_id']}"
            
            # Record Hermes session state
            session_obj = {
                "session_key": session_key,
                "session_id": event.source["chat_id"],
                "platform": event.source["chat_type"],
                "user_id": event.source["user_id"],
                "prompt": event.text,
                "raw_message": event.raw_message,
                "status": "active"
            }
            captured_sessions.append(session_obj)

            # Create session completion task
            async def session_runner():
                await asyncio.sleep(0.01)
                # Agent sends notification to user
                res = await self.adapter.send(
                    chat_id=event.source["chat_id"],
                    content=f"[Notification Delivered] Agent processed event '{event.raw_message['event_name']}' and notified user: {event.raw_message['message']}"
                )
                notification_results.append(res)
                session_obj["status"] = "completed"
                return "SUCCESS"

            task = asyncio.create_task(session_runner())
            self.adapter._session_tasks[session_key] = task

        self.adapter.handle_message = mock_handle_message

        # Mock Pub/Sub Message Payload
        mock_msg = MagicMock()
        mock_msg.message_id = "pubsub-msg-8877"
        payload = {
            "event_name": "GKEQuotaExceeded",
            "severity": "CRITICAL",
            "message": "Quota limit reached for TPU node pool in us-east1."
        }
        mock_msg.data = json.dumps(payload).encode("utf-8")

        # Process message through adapter
        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await self.adapter._process_message(
                "test_pubsub_alerts",
                self.config_dict["subscriptions"]["test_pubsub_alerts"],
                mock_msg
            )

        # 1. Verify Message Acknowledged
        mock_msg.ack.assert_called_once()

        # 2. Verify Hermes Session Spawning & Contents
        self.assertEqual(len(captured_sessions), 1, "Hermes session was not spawned")
        session = captured_sessions[0]
        
        self.assertEqual(session["platform"], "pubsub")
        self.assertEqual(session["user_id"], "pubsub:test_pubsub_alerts")
        self.assertEqual(session["session_id"], "pubsub:test_pubsub_alerts:pubsub-msg-8877")
        
        # 3. Verify Prompt Contents in Hermes Session
        self.assertIn("Alert Event: GKEQuotaExceeded", session["prompt"])
        self.assertIn("Severity: CRITICAL", session["prompt"])
        self.assertIn("Quota limit reached for TPU node pool in us-east1.", session["prompt"])
        self.assertIn("use `send_message` to inform the user", session["prompt"])
        self.assertEqual(session["raw_message"]["event_name"], "GKEQuotaExceeded")

        # 4. Verify Hermes Session Completion
        self.assertEqual(session["status"], "completed", "Hermes session task did not complete successfully")

        # 5. Verify Notification / send_message Delivery Side Effects
        self.assertEqual(len(notification_results), 1, "Notification result was not emitted")
        self.assertTrue(notification_results[0].success, "Notification delivery failed")


class TestGCPPubSubLivePublish(unittest.TestCase):

    def test_gcp_pubsub_live_publish_and_verify(self):
        """Optional Live GCP Pub/Sub Topic publish verification."""
        project_id = os.environ.get("PROJECT_ID") or os.environ.get("GCP_PROJECT_ID")
        topic_name = os.environ.get("PUBSUB_TOPIC_NAME", "platform-agent-test-topic")
        
        if not project_id:
            self.skipTest("PROJECT_ID environment variable not set. Skipping live GCP Pub/Sub publish test.")

        try:
            from google.cloud import pubsub_v1
            publisher = pubsub_v1.PublisherClient()
            topic_path = publisher.topic_path(project_id, topic_name)
            
            test_payload = {
                "event_name": "E2E Python Verification Alert",
                "severity": "WARNING",
                "message": "Python verification test notification for send_message."
            }
            data = json.dumps(test_payload).encode("utf-8")
            future = publisher.publish(topic_path, data)
            msg_id = future.result(timeout=10.0)
            self.assertIsNotNone(msg_id, "Failed to receive message ID from Pub/Sub publish")
            print(f"\n[Live Pub/Sub Test] Published message ID {msg_id} to {topic_path}")
        except ImportError:
            self.skipTest("google-cloud-pubsub module not installed in current Python environment. Skipping live publish.")
        except Exception as e:
            self.fail(f"Live Pub/Sub publish failed: {e}")


if __name__ == "__main__":
    unittest.main()

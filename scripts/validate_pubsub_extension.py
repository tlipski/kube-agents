#!/usr/bin/env python3
"""
Empirical Validation Script for PubSub Platform Extension.

Verifies:
1. General PubSub message receiving and prompt construction.
2. Programmatic validation_code execution:
   - Message passing validation (processed into agent session/prompt).
   - Message failing validation (invalidated, logged clearly, and skipped).
"""

import asyncio
import json
import logging
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root and extension directory are in sys.path
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


class TestPubSubEmpiricalValidation(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Configure PubSub route setups with programmatic validation_code
        self.config_dict = {
            "subscriptions": {
                "general_route": {
                    "topic": "platform-agent-test-topic",
                    "subscription": "platform-agent-test-sub",
                    "prompt": "General Event Notification: {event_name}\nDetails: {message}",
                    "deliver": "log"
                },
                "validated_route": {
                    "topic": "platform-agent-test-topic",
                    "subscription": "platform-agent-test-sub",
                    "prompt": "Validated Alert: {event_name} (Severity: {severity})\n{message}",
                    "validation_code": (
                        "def validate(payload, config):\n"
                        "    # Only process events with severity HIGH or CRITICAL\n"
                        "    sev = payload.get('severity', '').upper()\n"
                        "    return sev in ['HIGH', 'CRITICAL']\n"
                    ),
                    "deliver": "log"
                }
            }
        }
        self.cfg = DummyPlatformConfig(extra=self.config_dict)
        self.adapter = PubSubAdapter(self.cfg)

    async def test_general_pubsub_message_receive(self):
        """Feature 1: Verify general PubSub message receiving and prompt generation."""
        dispatched_events = []
        async def mock_handle_message(event):
            dispatched_events.append(event)

        self.adapter.handle_message = mock_handle_message

        mock_msg = MagicMock()
        mock_msg.message_id = "msg-gen-001"
        payload = {
            "event_name": "ClusterAutoscalerNotification",
            "message": "Node group scaled up by 2 instances."
        }
        mock_msg.data = json.dumps(payload).encode("utf-8")

        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await self.adapter._process_message("general_route", self.config_dict["subscriptions"]["general_route"], mock_msg)

        mock_msg.ack.assert_called_once()
        self.assertEqual(len(dispatched_events), 1, "General PubSub message was not received/dispatched")
        event = dispatched_events[0]
        self.assertIn("General Event Notification: ClusterAutoscalerNotification", event.text)
        self.assertIn("Node group scaled up by 2 instances.", event.text)
        print("\n✓ Feature 1 Passed: General PubSub message received and prompt generated successfully.")

    async def test_validation_code_passing_and_failing(self):
        """Feature 2: Verify programmatic validation_code with 2 test messages (one passing, one failing)."""
        dispatched_events = []
        async def mock_handle_message(event):
            dispatched_events.append(event)

        self.adapter.handle_message = mock_handle_message

        # --- Message 1: PASSING validation (severity: CRITICAL) ---
        mock_msg_pass = MagicMock()
        mock_msg_pass.message_id = "msg-val-pass"
        payload_pass = {
            "event_name": "GKEStorageQuotedExceeded",
            "severity": "CRITICAL",
            "message": "PersistentVolume claim exceeded threshold."
        }
        mock_msg_pass.data = json.dumps(payload_pass).encode("utf-8")

        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await self.adapter._process_message("validated_route", self.config_dict["subscriptions"]["validated_route"], mock_msg_pass)

        mock_msg_pass.ack.assert_called_once()
        self.assertEqual(len(dispatched_events), 1, "Passing message was incorrectly dropped by validation_code")
        self.assertIn("Validated Alert: GKEStorageQuotedExceeded", dispatched_events[0].text)
        print("✓ Feature 2a Passed: Validation code allowed PASSING message (severity: CRITICAL).")

        # --- Message 2: FAILING validation (severity: INFO) ---
        mock_msg_fail = MagicMock()
        mock_msg_fail.message_id = "msg-val-fail"
        payload_fail = {
            "event_name": "RoutineMaintenanceNotice",
            "severity": "INFO",
            "message": "Scheduled maintenance in 48 hours."
        }
        mock_msg_fail.data = json.dumps(payload_fail).encode("utf-8")

        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await self.adapter._process_message("validated_route", self.config_dict["subscriptions"]["validated_route"], mock_msg_fail)

        mock_msg_fail.ack.assert_called_once()
        self.assertEqual(len(dispatched_events), 1, "Failing message was incorrectly processed instead of being skipped")
        print("✓ Feature 2b Passed: Validation code invalidated and skipped FAILING message (severity: INFO).")


def run_gcp_live_validation(project_id: str, topic_name: str):
    """Publish test messages to live GCP PubSub topic for manual/automated empirical checking."""
    try:
        from google.cloud import pubsub_v1
    except ImportError:
        print("google-cloud-pubsub library not installed locally. Skipping live GCP topic publish.")
        return

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic_name)
    print(f"\nPublishing live test messages to GCP PubSub Topic: {topic_path}")

    # Message 1: General receive test message
    msg1 = {"event_name": "LiveGeneralTestEvent", "severity": "HIGH", "message": "Empirical live PubSub test message."}
    f1 = publisher.publish(topic_path, json.dumps(msg1).encode("utf-8"))
    print(f"Published general test message (ID: {f1.result(timeout=10)})")

    # Message 2: Passing validation test message
    msg2 = {"event_name": "LiveValidationPassEvent", "severity": "CRITICAL", "message": "Live validation passing message."}
    f2 = publisher.publish(topic_path, json.dumps(msg2).encode("utf-8"))
    print(f"Published passing validation test message (ID: {f2.result(timeout=10)})")

    # Message 3: Failing validation test message
    msg3 = {"event_name": "LiveValidationFailEvent", "severity": "INFO", "message": "Live validation failing message."}
    f3 = publisher.publish(topic_path, json.dumps(msg3).encode("utf-8"))
    print(f"Published failing validation test message (ID: {f3.result(timeout=10)})")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        proj = os.environ.get("PROJECT_ID", "tomeklipski-izrhgv")
        topic = sys.argv[2] if len(sys.argv) > 2 else "platform-agent-test-topic"
        run_gcp_live_validation(proj, topic)
    else:
        unittest.main()

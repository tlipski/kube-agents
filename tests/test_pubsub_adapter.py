#!/usr/bin/env python3
"""
Unit tests for PubSubAdapter (extensions/platforms/pubsub/adapter.py).
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root and extensions path are in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT_PATH = os.path.join(REPO_ROOT, "extensions", "pubsub-platform", "files", "platforms", "pubsub")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if EXT_PATH not in sys.path:
    sys.path.insert(0, EXT_PATH)

# Mock gateway modules if not present in the current python environment
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

# Inject mocks if gateway imports fail
try:
    import gateway.platforms.base
except ImportError:
    gw_module = MagicMock()
    gw_module.platforms.base.BasePlatformAdapter = DummyBasePlatformAdapter
    gw_module.platforms.base.SendResult = DummySendResult
    gw_module.platforms.base.MessageEvent = DummyMessageEvent
    gw_module.platforms.base.MessageType.TEXT = "text"
    gw_module.platforms.base.ProcessingOutcome = MagicMock()
    gw_module.config.Platform = DummyPlatform
    gw_module.config.PlatformConfig = DummyPlatformConfig
    gw_module.session.build_session_key = lambda src, **kw: f"session:{src.get('chat_id')}"
    sys.modules["gateway"] = gw_module
    sys.modules["gateway.platforms"] = gw_module.platforms
    sys.modules["gateway.platforms.base"] = gw_module.platforms.base
    sys.modules["gateway.config"] = gw_module.config
    sys.modules["gateway.session"] = gw_module.session

from adapter import PubSubAdapter, _get_nested_value, check_requirements, validate_config


class TestPubSubAdapterHelpers(unittest.TestCase):

    def test_get_nested_value(self):
        payload = {
            "incident": {
                "summary": "High CPU Usage",
                "resource": {"name": "pod-123"},
                "tags": ["critical", "k8s"]
            }
        }
        self.assertEqual(_get_nested_value(payload, "incident.summary"), "High CPU Usage")
        self.assertEqual(_get_nested_value(payload, "incident.resource.name"), "pod-123")
        self.assertEqual(_get_nested_value(payload, "incident.tags.0"), "critical")
        self.assertEqual(_get_nested_value(payload, "incident.tags.1"), "k8s")
        self.assertIsNone(_get_nested_value(payload, "incident.nonexistent"))
        self.assertIsNone(_get_nested_value(payload, "incident.tags.5"))

    def test_parse_topic_config(self):
        cfg = DummyPlatformConfig(extra={})
        adapter = PubSubAdapter(cfg)
        
        # Relative topic
        parsed = adapter._parse_topic_config({"topic": "my-topic"}, "my-project", "route1")
        self.assertEqual(parsed, ("projects/my-project/topics/my-topic", "my-topic"))
        
        # Absolute topic
        parsed_abs = adapter._parse_topic_config({"topic": "projects/other-proj/topics/other-topic"}, "my-project", "route1")
        self.assertEqual(parsed_abs, ("projects/other-proj/topics/other-topic", "other-topic"))
        
        # None topic
        self.assertIsNone(adapter._parse_topic_config({}, "my-project", "route1"))

    def test_parse_subscription_config(self):
        cfg = DummyPlatformConfig(extra={})
        adapter = PubSubAdapter(cfg)
        
        # Configured subscription name
        sub = adapter._parse_subscription_config({"subscription": "my-sub"}, "my-project", "my-topic")
        self.assertEqual(sub, "projects/my-project/subscriptions/my-sub")

        # Inferred subscription name from topic
        sub_inferred = adapter._parse_subscription_config({}, "my-project", "my-topic")
        self.assertEqual(sub_inferred, "projects/my-project/subscriptions/my-topic-sub")


class TestPubSubAdapterPromptRendering(unittest.TestCase):

    def setUp(self):
        cfg = DummyPlatformConfig(extra={})
        self.adapter = PubSubAdapter(cfg)

    def test_render_prompt_placeholders(self):
        template = "Alert: {event_name} - Severity: {severity}\nDetails: {message}\nUnknown: {missing}"
        payload = {
            "event_name": "Test Stockout",
            "severity": "WARNING",
            "message": "Pod cannot be scheduled on node pool",
        }
        rendered = self.adapter._render_prompt(template, payload, "test_route")
        self.assertIn("Alert: Test Stockout", rendered)
        self.assertIn("Severity: WARNING", rendered)
        self.assertIn("Details: Pod cannot be scheduled on node pool", rendered)
        self.assertIn("Unknown: {missing}", rendered)

    def test_render_prompt_raw_placeholder(self):
        template = "Raw payload:\n{__raw__}"
        payload = {"event_name": "Test Event", "code": 500}
        rendered = self.adapter._render_prompt(template, payload, "test_route")
        self.assertIn('"event_name": "Test Event"', rendered)
        self.assertIn('"code": 500', rendered)

    def test_render_prompt_default_fallback(self):
        payload = {"foo": "bar"}
        rendered = self.adapter._render_prompt("", payload, "my_route")
        self.assertIn("Pub/Sub notification on route 'my_route'", rendered)
        self.assertIn('"foo": "bar"', rendered)

    def test_eval_filter(self):
        payload = {"severity": "WARNING", "env": "prod"}
        self.assertTrue(self.adapter._eval_filter("severity == 'WARNING'", payload))
        self.assertFalse(self.adapter._eval_filter("severity == 'ERROR'", payload))
        self.assertTrue(self.adapter._eval_filter("severity == 'ERROR' or env == 'prod'", payload))
        self.assertTrue(self.adapter._eval_filter("severity == 'WARNING' and env == 'prod'", payload))
        self.assertFalse(self.adapter._eval_filter("severity == 'WARNING' and env == 'dev'", payload))

    def test_resource_presence_checks(self):
        try:
            from google.api_core.exceptions import NotFound
        except ImportError:
            class NotFound(Exception):
                pass

        mock_publisher = MagicMock()
        mock_subscriber = MagicMock()

        # 1. Resource exists
        self.assertTrue(self.adapter._check_topic_exists(mock_publisher, "projects/p/topics/t"))
        self.assertTrue(self.adapter._check_subscription_exists(mock_subscriber, "projects/p/subscriptions/s"))

        # 2. Resource missing (NotFound exception)
        with patch.dict(sys.modules, {"google.api_core.exceptions": MagicMock(NotFound=NotFound)}):
            mock_publisher.get_topic.side_effect = NotFound("Topic not found")
            mock_subscriber.get_subscription.side_effect = NotFound("Subscription not found")
            
            self.assertFalse(self.adapter._check_topic_exists(mock_publisher, "projects/p/topics/t"))
            self.assertFalse(self.adapter._check_subscription_exists(mock_subscriber, "projects/p/subscriptions/s"))
        
        # Verify NO creation methods were called on publisher or subscriber
        mock_publisher.create_topic.assert_not_called()
        mock_subscriber.create_subscription.assert_not_called()

    def test_validate_message_code(self):
        # Default with no validation_code
        payload = {"severity": "WARNING", "code": 100}
        sub_cfg_none = {}
        self.assertTrue(self.adapter._validate_message(payload, sub_cfg_none, "route1"))

        # Custom validation function returning True
        sub_cfg_true = {
            "validation_code": "def validate(payload, cfg):\n    return payload.get('severity') == 'WARNING'\n"
        }
        self.assertTrue(self.adapter._validate_message(payload, sub_cfg_true, "route1"))

        # Custom validation function returning False (e.g. false signal)
        sub_cfg_false = {
            "validation_code": "def validate(payload, cfg):\n    return payload.get('severity') == 'CRITICAL'\n"
        }
        self.assertFalse(self.adapter._validate_message(payload, sub_cfg_false, "route1"))

        # Custom validation snippet setting is_valid variable
        sub_cfg_var = {
            "validation_code": "is_valid = payload.get('code') > 50\n"
        }
        self.assertTrue(self.adapter._validate_message(payload, sub_cfg_var, "route1"))


class TestPubSubAdapterAsyncOperations(unittest.IsolatedAsyncioTestCase):

    async def test_send_log_delivery(self):
        cfg = DummyPlatformConfig(extra={})
        adapter = PubSubAdapter(cfg)
        adapter._delivery_info["pubsub:test:123"] = {"deliver": "log"}
        
        result = await adapter.send("pubsub:test:123", "Test response text")
        self.assertTrue(result.success)

    async def test_process_message_dispatches_event(self):
        config_dict = {
            "subscriptions": {
                "test_route": {
                    "topic": "test-topic",
                    "subscription": "test-sub",
                    "prompt": "Alert: {event_name}. Notify user using send_message: {message}",
                    "deliver": "log"
                }
            }
        }
        cfg = DummyPlatformConfig(extra=config_dict.get("subscriptions", {}))
        cfg.extra = config_dict
        adapter = PubSubAdapter(cfg)
        adapter.handle_message = AsyncMock()

        mock_msg = MagicMock()
        mock_msg.message_id = "msg-001"
        payload = {
            "event_name": "Test Alert Event",
            "message": "User notification required for test incident"
        }
        mock_msg.data = json.dumps(payload).encode("utf-8")

        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await adapter._process_message("test_route", config_dict["subscriptions"]["test_route"], mock_msg)

        mock_msg.ack.assert_called_once()
        adapter.handle_message.assert_called_once()
        dispatched_event = adapter.handle_message.call_args[0][0]
        self.assertIn("Test Alert Event", dispatched_event.text)
        self.assertIn("User notification required for test incident", dispatched_event.text)
        self.assertIn("send_message", dispatched_event.text)

        # Verify session source details on the dispatched event
        self.assertEqual(dispatched_event.source["chat_type"], "pubsub")
        self.assertEqual(dispatched_event.source["chat_id"], "pubsub:test_route:msg-001")
        self.assertEqual(dispatched_event.source["user_id"], "pubsub:test_route")

    async def test_pubsub_session_spawned_metadata(self):
        """Verify that an actual pubsub session with correct metadata is generated."""
        config_dict = {
            "subscriptions": {
                "gke_alerts": {
                    "topic": "gke-alerts",
                    "subscription": "gke-alerts-sub",
                    "prompt": "Alert: {event_name}",
                    "deliver": "log"
                }
            }
        }
        cfg = DummyPlatformConfig(extra=config_dict.get("subscriptions", {}))
        cfg.extra = config_dict
        adapter = PubSubAdapter(cfg)

        dispatched_events = []
        async def mock_handle_message(event):
            dispatched_events.append(event)

        adapter.handle_message = mock_handle_message

        mock_msg = MagicMock()
        mock_msg.message_id = "alert-session-999"
        mock_msg.data = json.dumps({"event_name": "NodeOutOfMemory"}).encode("utf-8")

        with patch.dict(os.environ, {"DISABLE_PUBSUB_DEDUP": "true"}):
            await adapter._process_message("gke_alerts", config_dict["subscriptions"]["gke_alerts"], mock_msg)

        self.assertEqual(len(dispatched_events), 1)
        event = dispatched_events[0]
        
        # Assert session key & chat parameters spawned for Hermes agent
        expected_chat_id = "pubsub:gke_alerts:alert-session-999"
        self.assertEqual(event.source["chat_id"], expected_chat_id)
        self.assertEqual(event.source["chat_type"], "pubsub")
        self.assertEqual(event.source["user_id"], "pubsub:gke_alerts")

        # Verify session delivery configuration mapping
        delivery_info = adapter._delivery_info.get(expected_chat_id)
        self.assertIsNotNone(delivery_info)
        self.assertEqual(delivery_info["deliver"], "log")


if __name__ == "__main__":
    unittest.main()

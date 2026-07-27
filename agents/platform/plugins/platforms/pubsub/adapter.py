import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

os.environ["PUBSUB_HOME_CHANNEL"] = "none"
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy import helper to verify dependencies
def check_requirements() -> bool:
    try:
        from google.cloud import pubsub_v1
        return True
    except ImportError:
        return False

def validate_config(config) -> bool:
    return True

# Import main base classes
from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    MessageEvent,
    MessageType,
    ProcessingOutcome,
)
from gateway.config import Platform, PlatformConfig


def _get_nested_value(payload: Any, path: str) -> Any:
    parts = path.split(".")
    val = payload
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        elif isinstance(val, list):
            try:
                idx = int(part)
                val = val[idx] if idx < len(val) else None
            except ValueError:
                return None
        else:
            return None
    return val



def _create_session_sync(daemon_url: str, bearer_token: str, owner: str) -> str:
    url = f"{daemon_url.rstrip('/')}/sessions"
    req = urllib.request.Request(url, data=b"", method="POST")
    if bearer_token:
        req.add_header("Authorization", f"Bearer {bearer_token}")
    if owner:
        req.add_header("X-Asserted-Caller", owner)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        session_id = data.get("sessionID", "")
        if not session_id:
            raise RuntimeError("POST /sessions returned empty sessionID")
        return session_id


def _inject_prompt_sync(daemon_url: str, session_id: str, prompt: str, bearer_token: str, owner: str, alert_msg: Optional[str] = None) -> None:
    url = f"{daemon_url.rstrip('/')}/sessions/{session_id}/inject"
    msg_payload = {"prompt": prompt}
    if alert_msg:
        msg_payload["alertMsg"] = alert_msg
    payload = {"message": msg_payload}
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data_bytes, headers={"Content-Type": "application/json"}, method="POST"
    )
    if bearer_token:
        req.add_header("Authorization", f"Bearer {bearer_token}")
    if owner:
        req.add_header("X-Asserted-Caller", owner)
    with urllib.request.urlopen(req, timeout=10.0) as resp:
        if resp.status < 200 or resp.status >= 300:
            resp_body = resp.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"POST inject status {resp.status}: {resp_body}")


class PubSubAdapter(BasePlatformAdapter):
    """Google Cloud Pub/Sub pull subscriber adapter for Hermes Agent.
    
    This platform adapter connects to GCP Pub/Sub pull subscriptions, consumes incoming 
    messages (e.g. log-based alerts from Cloud Logging sinks), formats them using custom 
    templates, optionally wraps them into agent skill invocations, and routes them to the 
    core agent conversation loop.
    
    It also supports sending agent responses back to other chat platforms (cross-platform 
    delivery) or writing them to the system logs.
    """

    def __init__(self, config: PlatformConfig):
        """Initialize the adapter with PlatformConfig configurations."""
        super().__init__(config, Platform("pubsub"))
        extra = config.extra or {}
        self._subscriptions_config: Dict[str, dict] = extra.get("subscriptions", {})
        self._subscriber: Optional[Any] = None
        self._streaming_pull_futures: List[Any] = []
        self._delivery_info: Dict[str, dict] = {}
        self.gateway_runner = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._message_timestamps: Dict[str, List[float]] = {}
        self._daemon_url: str = (
            extra.get("daemon_url")
            or os.environ.get("DAEMON_URL")
            or os.environ.get("PLATFORM_DAEMON_URL")
            or "http://localhost:8699"
        )

        token_env_var = extra.get("token_env", "")
        token_from_env = os.environ.get(token_env_var, "") if token_env_var else ""
        self._bearer_token: str = (
            extra.get("bearer_token")
            or token_from_env
            or os.environ.get("AGENT_BEARER_TOKEN", "")
        )
        self._owner: str = (
            extra.get("owner")
            or extra.get("asserted_caller")
            or os.environ.get("ASSERTED_CALLER", "")
        )
        self._mode: str = extra.get("mode", "per-incident")
        self._target_session: str = extra.get("target_session", "")

    async def _create_session(self, owner: Optional[str] = None) -> str:
        """Call POST /sessions to create a new session ID via daemon HTTP API."""
        daemon_url = self._daemon_url
        bearer_token = self._bearer_token
        asserted_owner = owner or self._owner
        return await asyncio.to_thread(_create_session_sync, daemon_url, bearer_token, asserted_owner)

    async def _inject_prompt(self, session_id: str, prompt: str, owner: Optional[str] = None, alert_msg: Optional[str] = None) -> None:
        """Call POST /sessions/{session_id}/inject with prompt payload via daemon HTTP API."""
        daemon_url = self._daemon_url
        bearer_token = self._bearer_token
        asserted_owner = owner or self._owner
        await asyncio.to_thread(_inject_prompt_sync, daemon_url, session_id, prompt, bearer_token, asserted_owner, alert_msg)


    def _get_project_id(self) -> Optional[str]:
        """Detect GCP Project ID from auth credentials or environment variables."""
        try:
            import google.auth
            _, project_id = google.auth.default()
            if not project_id:
                project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
            return project_id
        except Exception as e:
            logger.warning("PubSub: Failed to detect GCP project ID: %s", e)
            return None

    def _parse_topic_config(self, sub_cfg: dict, project_id: str, name: str) -> Optional[tuple]:
        """Parse topic path and simple name from config."""
        topic_input = sub_cfg.get("topic")
        if not topic_input:
            return None
        if topic_input.startswith("projects/"):
            return topic_input, topic_input.split("/")[-1]
        return f"projects/{project_id}/topics/{topic_input}", topic_input

    def _parse_subscription_config(self, sub_cfg: dict, project_id: str, topic_name: Optional[str]) -> str:
        """Parse or generate subscription path based on topic or configured subscription name."""
        sub_input = sub_cfg.get("subscription")
        if not sub_input:
            if not topic_name:
                raise ValueError("Either 'topic' or 'subscription' must be configured.")
            sub_name = f"{topic_name}-sub"
            return f"projects/{project_id}/subscriptions/{sub_name}"
        if sub_input.startswith("projects/"):
            return sub_input
        return f"projects/{project_id}/subscriptions/{sub_input}"

    def _ensure_topic_exists(self, publisher, topic_path: str) -> None:
        """Verify the Pub/Sub topic exists or create it if not found."""
        from google.api_core.exceptions import NotFound
        try:
            publisher.get_topic(request={"topic": topic_path})
            logger.info("PubSub: Topic '%s' already exists", topic_path)
        except NotFound:
            logger.info("PubSub: Creating topic '%s'", topic_path)
            publisher.create_topic(request={"name": topic_path})

    def _ensure_subscription_exists(self, subscriber, sub_path: str, topic_path: str) -> None:
        """Verify the subscription exists on the topic or create it if not found."""
        from google.api_core.exceptions import NotFound
        try:
            subscriber.get_subscription(request={"subscription": sub_path})
            logger.info("PubSub: Subscription '%s' already exists", sub_path)
        except NotFound:
            logger.info("PubSub: Creating subscription '%s' on topic '%s'", sub_path, topic_path)
            subscriber.create_subscription(request={"name": sub_path, "topic": topic_path})

    def _ensure_log_sink(self, project_id: str, name: str, topic_path: str, query: str) -> Optional[str]:
        """Ensure GCP Log Sink exists and routes log entries matching the query to the topic."""
        from google.cloud import logging as gcp_logging
        logging_client = gcp_logging.Client(project=project_id)
        sink_name = f"hermes-pubsub-{name}-sink"
        destination = f"pubsub.googleapis.com/{topic_path}"
        sink = logging_client.sink(sink_name, filter_=query, destination=destination)
        
        try:
            if sink.exists():
                logger.info("PubSub: Log sink '%s' already exists", sink_name)
                sink.reload()
                if sink.filter_ != query or sink.destination != destination:
                    logger.info("PubSub: Updating log sink '%s' with filter '%s'", sink_name, query)
                    sink.filter_ = query
                    sink.destination = destination
                    sink.update()
            else:
                logger.info("PubSub: Creating log sink '%s'", sink_name)
                sink.create()
            return sink.writer_identity
        except Exception as e:
            logger.error("PubSub: Failed to ensure Log Sink '%s': %s", sink_name, e)
            raise e

    def _grant_sink_publisher_role(self, publisher, topic_path: str, writer_identity: str) -> None:
        """Grant Pub/Sub Publisher role to the log sink's service account identity."""
        logger.info("PubSub: Granting pubsub.publisher to log sink identity: %s", writer_identity)
        policy = publisher.get_iam_policy(request={"resource": topic_path})
        
        binding_found = False
        for binding in policy.bindings:
            if binding.role == "roles/pubsub.publisher":
                if writer_identity in binding.members:
                    binding_found = True
                    break
                else:
                    binding.members.append(writer_identity)
                    binding_found = True
                    publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})
                    break
                    
        if not binding_found:
            from google.iam.v1 import policy_pb2
            binding = policy_pb2.Binding(
                role="roles/pubsub.publisher",
                members=[writer_identity]
            )
            policy.bindings.append(binding)
            publisher.set_iam_policy(request={"resource": topic_path, "policy": policy})

    def _ensure_resources(self, name: str, sub_cfg: dict, project_id: str) -> str:
        """Provision or auto-verify GCP Pub/Sub topics, subscriptions, and stack log sinks."""
        from google.cloud import pubsub_v1
        
        topic_info = self._parse_topic_config(sub_cfg, project_id, name)
        if not topic_info:
            logger.info("PubSub: No 'topic' configured for route '%s', skipping GCP resource provisioning.", name)
            return self._parse_subscription_config(sub_cfg, project_id, None)
            
        topic_path, topic_name = topic_info
        sub_path = self._parse_subscription_config(sub_cfg, project_id, topic_name)

        publisher = pubsub_v1.PublisherClient()
        subscriber = pubsub_v1.SubscriberClient()
        
        self._ensure_topic_exists(publisher, topic_path)
        self._ensure_subscription_exists(subscriber, sub_path, topic_path)
            
        query = sub_cfg.get("query")
        if query:
            writer_identity = self._ensure_log_sink(project_id, name, topic_path, query)
            if writer_identity:
                self._grant_sink_publisher_role(publisher, topic_path, writer_identity)
                    
        return sub_path

    def _resolve_subscription_path(self, name: str, sub_cfg: dict, project_id: Optional[str]) -> Optional[str]:
        """Resolve full GCP resource path for a configured subscription name/route."""
        subscription_path = None
        if project_id:
            try:
                subscription_path = self._ensure_resources(name, sub_cfg, project_id)
            except Exception as e:
                logger.warning("PubSub: Resource auto-provisioning failed for '%s': %s. Falling back to configured path.", name, e)
        
        if not subscription_path:
            subscription_path = sub_cfg.get("subscription")
            if subscription_path and not subscription_path.startswith("projects/") and project_id:
                subscription_path = f"projects/{project_id}/subscriptions/{subscription_path}"
        return subscription_path

    def _subscribe_to_route(self, name: str, sub_cfg: dict, subscription_path: str, loop: asyncio.AbstractEventLoop) -> None:
        """Initiate asynchronous background pull streaming subscription on the path."""
        logger.info("PubSub: Subscribing to '%s' (route '%s')", subscription_path, name)

        def make_callback(route_name=name, cfg=sub_cfg):
            def callback(message):
                asyncio.run_coroutine_threadsafe(
                    self._process_message(route_name, cfg, message),
                    loop
                )
            return callback

        try:
            future = self._subscriber.subscribe(
                subscription_path,
                callback=make_callback(name, sub_cfg)
            )
            self._streaming_pull_futures.append(future)
        except Exception as e:
            logger.error("PubSub: Failed to subscribe to '%s': %s", subscription_path, e)

    async def connect(self, is_reconnect: bool = False, **kwargs) -> bool:
        """Establish connection and start listening to all configured Pub/Sub subscriptions."""
        if not self._subscriptions_config:
            logger.warning("PubSub: No subscriptions configured in config.yaml under platforms.pubsub.extra.subscriptions")
            return True

        if not check_requirements():
            logger.error("PubSub: google-cloud-pubsub library is not installed")
            self._set_fatal_error("dependencies_missing", "google-cloud-pubsub not installed", retryable=False)
            return False

        from google.cloud import pubsub_v1

        try:
            self._subscriber = pubsub_v1.SubscriberClient()
        except Exception as e:
            logger.error("PubSub: Failed to initialize SubscriberClient: %s", e)
            self._set_fatal_error("client_init_failed", str(e), retryable=True)
            return False

        project_id = self._get_project_id()
        loop = asyncio.get_running_loop()

        for name, sub_cfg in self._subscriptions_config.items():
            subscription_path = self._resolve_subscription_path(name, sub_cfg, project_id)
            if not subscription_path:
                logger.error("PubSub: Route '%s' missing 'subscription' path", name)
                continue
            self._subscribe_to_route(name, sub_cfg, subscription_path, loop)

        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        """Disconnect and clean up background subscription pulls."""
        self._running = False
        for future in self._streaming_pull_futures:
            try:
                future.cancel()
            except Exception:
                pass
        self._streaming_pull_futures.clear()
        if self._subscriber:
            try:
                self._subscriber.close()
            except Exception:
                pass
        self._mark_disconnected()
        logger.info("PubSub: Disconnected")

    async def _send_to_platform(self, deliver_type: str, delivery: dict, content: str) -> SendResult:
        """Deliver the generated response to a target platform (cross-platform routing)."""
        from gateway.config import Platform as CorePlatform
        target_platform = CorePlatform(deliver_type)
        adapter = self.gateway_runner.adapters.get(target_platform)
        if not adapter:
            return SendResult(success=False, error=f"Adapter for platform {deliver_type} not found")
            
        extra = delivery.get("deliver_extra", {})
        chat_id_target = extra.get("chat_id", "")
        if not chat_id_target:
            home = self.gateway_runner.config.get_home_channel(target_platform)
            if home:
                chat_id_target = home.chat_id
            else:
                return SendResult(
                    success=False,
                    error=f"No chat_id or home channel for {deliver_type}",
                )

        thread_id = extra.get("message_thread_id") or extra.get("thread_id")
        meta = None
        if thread_id:
            meta = {"thread_id": thread_id}

        return await adapter.send(chat_id_target, content, metadata=meta)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Process outbox message routing back to log or cross-platform targets."""
        delivery = self._delivery_info.get(chat_id, {})
        deliver_type = delivery.get("deliver", "log")

        if deliver_type == "log":
            logger.info("PubSub: Response for %s: %s", chat_id, content[:200])
            return SendResult(success=True)

        if self.gateway_runner:
            try:
                return await self._send_to_platform(deliver_type, delivery, content)
            except Exception as e:
                logger.error("PubSub: Cross-platform delivery to %s failed: %s", deliver_type, e)
                return SendResult(success=False, error=str(e))

        logger.warning("PubSub: Unknown or unconfigured deliver type: %s", deliver_type)
        return SendResult(success=False, error=f"Unknown deliver type: {deliver_type}")



    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic metadata about the PubSub route channel."""
        return {"name": chat_id, "type": "pubsub"}

    def _parse_message_payload(self, message) -> dict:
        """Decode and parse incoming message body bytes (expect JSON)."""
        data_str = message.data.decode("utf-8")
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            return {"text": data_str}

    def _apply_skills_to_prompt(self, prompt: str, skills: List[str]) -> str:
        """Optionally prepend or wrap the prompt text inside specific skill invocations."""
        try:
            from agent.skill_commands import (
                build_skill_invocation_message,
                get_skill_commands,
            )
            skill_cmds = get_skill_commands()
            for skill_name in skills:
                cmd_key = f"/{skill_name}"
                if cmd_key in skill_cmds:
                    skill_content = build_skill_invocation_message(
                        cmd_key, user_instruction=prompt
                    )
                    if skill_content:
                        prompt = skill_content
                else:
                    logger.warning("PubSub: Skill '%s' not found", skill_name)
        except Exception as e:
            logger.warning("PubSub: Skill loading failed: %s", e)
        return prompt



    def _eval_filter(self, expression: str, payload: dict) -> bool:
        """Evaluate a simple boolean filter expression against the payload."""
        try:
            or_parts = [op.strip() for op in expression.split(" or ")]
            for or_part in or_parts:
                if not or_part:
                    continue
                
                # Evaluate and-part
                and_match = True
                parts = [p.strip() for p in or_part.split(" and ")]
                for part in parts:
                    if not part:
                        continue
                    match = re.match(r"^([a-zA-Z0-9_.]+)\s*(==|!=)\s*[\'\"']?(.*?)[\'\"']?$", part)
                    if not match:
                        logger.warning("PubSub: Invalid filter expression format: %s", part)
                        and_match = False
                        break
                    path, op, expected = match.groups()
                    expected = expected.strip("'\"")
                    
                    # Resolve path
                    val = payload
                    for key in path.split("."):
                        if isinstance(val, dict):
                            val = val.get(key)
                        else:
                            val = None
                            break
                    actual = str(val) if val is not None else ""
                    logger.warning("PubSub: Filter check '%s' %s '%s' (actual: '%s')", path, op, expected, actual)
                    
                    if op == "==":
                        if actual != expected:
                            and_match = False
                            break
                    elif op == "!=":
                        if actual == expected:
                            and_match = False
                            break
                
                # If all AND conditions in this OR part matched, the filter passes!
                if and_match:
                    return True
                    
            return False
        except Exception as e:
            logger.error("PubSub: Exception evaluating filter '%s': %s", expression, e)
            return False

    def _get_registry_path(self) -> str:
        # Check if /opt/data is writable
        path = "/opt/data/pubsub_registry.json"
        if os.path.exists("/opt/data") and os.access("/opt/data", os.W_OK):
            return path
        return "./pubsub_registry.json"

    def _load_registry(self) -> List[dict]:
        path = self._get_registry_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("PubSub: Failed to load registry: %s", e)
            return []

    def _save_registry(self, registry: List[dict]) -> None:
        path = self._get_registry_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(registry, f, indent=2)
        except Exception as e:
            logger.warning("PubSub: Failed to save registry: %s", e)

    def _is_false_signal(self, payload: dict, subscription_config: dict) -> bool:
        """Programmatically check if the alert is a false signal (no active pending pods)."""
        import subprocess
        # Extract namespace and workload name using the configured deduplicate fields
        dedup_fields = subscription_config.get("deduplicate_fields", [])
        if not dedup_fields or len(dedup_fields) < 3:
            return False  # Cannot check without dedup fields
            
        ns_field = dedup_fields[1]
        workload_field = dedup_fields[2]
        
        namespace = _get_nested_value(payload, ns_field)
        workload_name = _get_nested_value(payload, workload_field)
        
        if not namespace or not workload_name:
            logger.info("PubSub: Could not extract namespace or workload name from payload for false signal check.")
            return False
            
        logger.info("PubSub: Running programmatic false signal check for workload '%s' in namespace '%s'...", workload_name, namespace)
        
        try:
            cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
            env = os.environ.copy()
            env["KUBECONFIG"] = "/dev/null"
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            if result.returncode != 0:
                logger.warning("PubSub: kubectl command failed during false signal check: %s", result.stderr)
                return False
                
            pods_data = json.loads(result.stdout)
            items = pods_data.get("items", [])
            
            active_pending = False
            workload_found = False
            
            for pod in items:
                pod_name = pod.get("metadata", {}).get("name", "")
                if pod_name.startswith(workload_name):
                    workload_found = True
                    phase = pod.get("status", {}).get("phase", "")
                    if phase == "Pending":
                        active_pending = True
                        break
            
            if not workload_found:
                logger.warning("PubSub: Workload '%s' not found in local namespace '%s'. Proceeding with agent trigger.", workload_name, namespace)
                return False
                
            if not active_pending:
                logger.warning("PubSub: Workload '%s' has no pending pods in namespace '%s'. Treating as false/resolved signal.", workload_name, namespace)
                return True
                
            logger.warning("PubSub: Workload '%s' has active pending pods. Proceeding with agent trigger.", workload_name)
            return False
            
        except Exception as e:
            logger.warning("PubSub: Exception during programmatic false signal check: %s", e)
            return False

    async def _process_message(self, route_name: str, route_config: dict, message):
        """Asynchronously process incoming raw pub/sub pull messages with threshold, lock, and deduplication."""
        try:
            # Acknowledge immediately to prevent redeliveries during long agent run
            message.ack()

            payload = self._parse_message_payload(message)

            filter_expr = route_config.get("filter")
            if filter_expr:
                if not self._eval_filter(filter_expr, payload):
                    logger.warning("PubSub: Message on route '%s' filtered out by expression '%s'", route_name, filter_expr)
                    return

            # --- Level 1: Filter threshold on the error message count appearing ---
            threshold_count = route_config.get("threshold_count")
            if threshold_count is not None and not os.environ.get("DISABLE_PUBSUB_DEDUP", "false").lower() == "true":
                now = time.time()
                window = route_config.get("threshold_window_seconds", 300)
                
                if route_name not in self._message_timestamps:
                    self._message_timestamps[route_name] = []
                
                self._message_timestamps[route_name].append(now)
                self._message_timestamps[route_name] = [
                    t for t in self._message_timestamps[route_name] if now - t <= window
                ]
                
                current_count = len(self._message_timestamps[route_name])
                if current_count < threshold_count:
                    logger.warning(
                        "PubSub: Message on route '%s' ignored. Count in window is %d/%d (threshold not met).",
                        route_name, current_count, threshold_count
                    )
                    return
                else:
                    logger.warning(
                        "PubSub: Message on route '%s' passed threshold count check: %d/%d in %ds window.",
                        route_name, current_count, threshold_count, window
                    )

            logger.warning("PubSub: Received message on route '%s'", route_name)

            # --- Level 2: Lock and Deduplication Registry ---
            if route_name not in self._locks:
                self._locks[route_name] = asyncio.Lock()

            async with self._locks[route_name]:
                dedup_fields = route_config.get("deduplicate_fields")
                if dedup_fields and not os.environ.get("DISABLE_PUBSUB_DEDUP", "false").lower() == "true":
                    current_values = {}
                    for field in dedup_fields:
                        val = _get_nested_value(payload, field)
                        current_values[field] = str(val) if val is not None else ""

                    registry = self._load_registry()
                    now = time.time()
                    dedup_window = route_config.get("deduplicate_window_seconds", 86400)  # 24 hours default
                    
                    cleaned_registry = []
                    is_duplicate = False
                    for entry in registry:
                        if now - entry.get("timestamp", 0) < dedup_window:
                            cleaned_registry.append(entry)
                            if entry.get("route_name") == route_name:
                                if entry.get("field_values") == current_values:
                                    is_duplicate = True
                    
                    if is_duplicate:
                        logger.warning(
                            "PubSub: Duplicate message detected on route '%s' using fields %s. Skipping prompt triggering.",
                            route_name, dedup_fields
                        )
                        self._save_registry(cleaned_registry)
                        return

                    # --- Level 3: Programmatic False Signal Check ---
                    if self._is_false_signal(payload, route_config):
                        logger.warning(
                            "PubSub: Message on route '%s' identified as a false signal and the workload pods are currently healthy. Skipping prompt triggering.",
                            route_name
                        )
                        # Register it so that subsequent duplicates of this false signal are also ignored
                        new_entry = {
                            "route_name": route_name,
                            "timestamp": now,
                            "field_values": current_values
                        }
                        cleaned_registry.append(new_entry)
                        self._save_registry(cleaned_registry)
                        return

                    # Update registry for a valid active alert
                    new_entry = {
                        "route_name": route_name,
                        "timestamp": now,
                        "field_values": current_values
                    }
                    cleaned_registry.append(new_entry)
                    self._save_registry(cleaned_registry)

                prompt_template = route_config.get("prompt", "")
                prompt = self._render_prompt(prompt_template, payload, route_name)

                skills = route_config.get("skills", [])
                if skills:
                    prompt = self._apply_skills_to_prompt(prompt, skills)

                daemon_url = route_config.get("daemon_url") or self._daemon_url
                mode = route_config.get("mode") or self._mode
                route_owner = route_config.get("owner") or self._owner

                if mode == "per-incident":
                    session_id = await self._create_session(owner=route_owner)
                else:
                    session_id = route_config.get("target_session") or self._target_session
                    if not session_id:
                        raise ValueError("PubSub: 'target_session' is required when mode is 'shared'")

                alert_msg = route_config.get("alert_msg") or payload.get("event_name")
                if alert_msg and isinstance(alert_msg, str):
                    alert_msg = self._render_prompt(alert_msg, payload, route_name)

                logger.info("PubSub: Injecting prompt to session %s on daemon %s (mode=%s)", session_id, daemon_url, mode)
                await self._inject_prompt(session_id, prompt, owner=route_owner, alert_msg=alert_msg)



        except Exception as e:
            logger.exception("PubSub: Error in _process_message on route %s: %s", route_name, e)

    def _render_prompt(
        self,
        template: str,
        payload: dict,
        route_name: str,
    ) -> str:
        """Resolve placeholders in prompt template with matching payload fields."""
        if not template:
            truncated = json.dumps(payload, indent=2)[:4000]
            return (
                f"Pub/Sub notification on route "
                f"'{route_name}':\n\n```json\n{truncated}\n```"
            )

        def _resolve(match: re.Match) -> str:
            key = match.group(1)
            if key == "__raw__":
                return json.dumps(payload, indent=2)[:4000]
            value: Any = payload
            for part in key.split("."):
                if isinstance(value, dict):
                    value = value.get(part, f"{{{key}}}")
                else:
                    return f"{{{key}}}"
            if isinstance(value, (dict, list)):
                return json.dumps(value, indent=2)[:2000]
            return str(value)

        return re.sub(r"\{([a-zA-Z0-9_.]+)\}", _resolve, template)

    def _render_delivery_extra(
        self, extra: dict, payload: dict
    ) -> dict:
        """Resolve fields in deliver_extra config using payload keys dynamically."""
        rendered: Dict[str, Any] = {}
        for key, value in extra.items():
            if isinstance(value, str):
                rendered[key] = self._render_prompt(value, payload, "")
            else:
                rendered[key] = value
        return rendered


def register(ctx):
    """Register the pubsub platform adapter with the gateway context."""
    ctx.register_platform(
        name="pubsub",
        label="PubSub",
        adapter_factory=lambda cfg: PubSubAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=[],
        install_hint="pip install google-cloud-pubsub",
        max_message_length=0,
        emoji="🔔",
        pii_safe=True,
        allow_update_command=False,
        platform_hint="You are receiving events via Google Cloud Pub/Sub pull subscription.",
        allowed_users_env="PUBSUB_ALLOWED_USERS",
        allow_all_env="PUBSUB_ALLOW_ALL_USERS",
    )

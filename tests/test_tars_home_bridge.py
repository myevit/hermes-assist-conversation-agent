"""Contract tests for the local HA -> TARS Home API bridge."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


BRIDGE_PATH = Path(__file__).parents[1] / "bridge" / "bridge.py"
SPEC = importlib.util.spec_from_file_location("tars_home_bridge", BRIDGE_PATH)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class FakeResponse:
    def __init__(self, payload: dict, *, status: int = 200):
        self.payload = payload
        self.status = status
        self.headers = {"X-Hermes-Session-Id": "session-from-gateway"}

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_client_posts_only_to_tars_home_profile_and_preserves_session():
    client = bridge.HermesApiClient("http://127.0.0.1:8642", "secret-key", conversation_prefix="ha")
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return FakeResponse({"output": [{"type": "message", "content": [{"type": "output_text", "text": "Lights on."}]}]})

    with patch.object(bridge.request, "urlopen", fake_urlopen):
        reply = client.ask("Turn the kitchen lights on", "abc-123")

    assert reply.text == "Lights on."
    assert reply.session_id == "session-from-gateway"
    assert captured["url"] == "http://127.0.0.1:8642/p/tars-home/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"
    assert captured["headers"]["X-hermes-session-id"] == "ha-abc-123"
    assert captured["body"] == {"input": "Turn the kitchen lights on", "conversation": "ha-abc-123", "store": True, "truncation": "auto"}
    assert captured["timeout"] == bridge.DEFAULT_TIMEOUT_SECONDS


def test_client_rejects_profile_escape_and_never_logs_secret():
    with pytest.raises(ValueError, match="loopback"):
        bridge.HermesApiClient("https://gateway.example", "secret-key")
    with pytest.raises(ValueError, match="not configured"):
        bridge.HermesApiClient("http://127.0.0.1:8642", "")
    failure = bridge.HermesBridgeError("upstream failed: secret-key", status=502)
    assert "secret-key" not in bridge.HermesApiClient.safe_error(failure)
    assert "secret-key" not in str(failure.safe_message())


def test_client_extracts_output_text_from_standard_responses_shape():
    assert bridge.extract_output_text({"output_text": "Preferred fast path"}) == "Preferred fast path"
    assert bridge.extract_output_text({"output": [{"type": "message", "content": [{"type": "output_text", "text": "Parsed fallback"}]}]}) == "Parsed fallback"
    with pytest.raises(bridge.HermesBridgeError, match="no usable text"):
        bridge.extract_output_text({"output": []})


def test_conversation_id_is_bounded_and_collision_resistant():
    assert bridge.normalise_conversation_id("ha:ABC / child") == "ha-ABC-child"
    long_a = bridge.normalise_conversation_id("x" * 500 + "a")
    long_b = bridge.normalise_conversation_id("x" * 500 + "b")
    assert len(long_a) <= bridge.MAX_CONVERSATION_ID_LENGTH
    assert long_a != long_b
    with pytest.raises(ValueError, match="required"):
        bridge.normalise_conversation_id(" / ")


def test_client_rejects_oversized_gateway_response():
    client = bridge.HermesApiClient("http://127.0.0.1:8642", "secret-key")
    oversized = {"output_text": "x" * (bridge.MAX_RESPONSE_BYTES + 1)}
    with patch.object(bridge.request, "urlopen", lambda *_args, **_kwargs: FakeResponse(oversized)):
        with pytest.raises(bridge.HermesBridgeError, match="exceeds bridge limit"):
            client.ask("hello", "conversation-1")


def test_channel_auth_is_only_bypassed_in_explicit_test_mode(monkeypatch):
    monkeypatch.setattr(bridge, "TEST_MODE", False)
    assert bridge.bridge_auth_required() is True
    monkeypatch.setattr(bridge, "TEST_MODE", True)
    assert bridge.bridge_auth_required() is False


def test_ha_adapter_does_not_collect_or_send_a_bridge_api_key():
    root = BRIDGE_PATH.parents[1]
    config_flow = (root / "custom_components" / "hermes_assist_conversation" / "config_flow.py").read_text()
    conversation = (root / "custom_components" / "hermes_assist_conversation" / "conversation.py").read_text()
    assert "CONF_API_KEY" not in config_flow
    assert "CONF_API_KEY" not in conversation
    assert '"Authorization"' not in conversation

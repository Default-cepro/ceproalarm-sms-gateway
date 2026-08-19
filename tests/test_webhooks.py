import asyncio
import hashlib
import hmac
import json
import time

import httpx
import pytest

from src.api import state, webhooks
from src.api.app import app


class _Headers:
    def __init__(self, items):
        self._items = {k.lower(): v for k, v in items.items()}

    def get(self, key, default=None):
        return self._items.get(key.lower(), default)


class _Request:
    def __init__(self, headers):
        self.headers = _Headers(headers)


def _signature(raw_body: bytes, timestamp: str, key: str) -> str:
    mac = hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(raw_body)
    mac.update(timestamp.encode("utf-8"))
    return mac.hexdigest()


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_state():
    state.recent_delivery_ids_order.clear()
    state.recent_delivery_ids_set.clear()
    state.recent_incoming_message_ids_order.clear()
    state.recent_incoming_message_ids_set.clear()
    state.recent_status_event_keys_order.clear()
    state.recent_status_event_keys_set.clear()
    yield
    state.recent_delivery_ids_order.clear()
    state.recent_delivery_ids_set.clear()
    state.recent_incoming_message_ids_order.clear()
    state.recent_incoming_message_ids_set.clear()
    state.recent_status_event_keys_order.clear()
    state.recent_status_event_keys_set.clear()


async def test_get_events_returns_success(client):
    resp = await client.get("/webhook/sms/events")
    assert resp.status_code == 200
    assert resp.json()["payload"]["source"] == "sms-gate-events"


async def test_post_sms_received_resolves_pending(client, monkeypatch):
    fut = asyncio.get_event_loop().create_future()
    entry = {"id": "evt-cmd", "future": fut, "match_fn": lambda msg: True}
    monkeypatch.setitem(state.pending_commands, "04141234567", [entry])

    envelope = {
        "event": "sms:received",
        "id": "evt-1",
        "payload": {
            "phoneNumber": "04141234567",
            "message": "STATUS",
            "messageId": "m-1",
        },
    }
    resp = await client.post("/webhook/sms/events", json=envelope)
    assert resp.status_code == 200
    body = resp.json()
    assert body["payload"]["event"] == "sms:received"

    assert fut.done()
    result = fut.result()
    assert result["message"] == "STATUS"


async def test_post_duplicate_delivery_returns_duplicate(client):
    envelope = {
        "event": "sms:received",
        "id": "evt-dup",
        "payload": {
            "phoneNumber": "04141234567",
            "message": "STATUS",
            "messageId": "m-dup",
        },
    }
    resp1 = await client.post("/webhook/sms/events", json=envelope)
    assert resp1.status_code == 200

    resp2 = await client.post("/webhook/sms/events", json=envelope)
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["payload"]["duplicate"] is True


async def test_signature_enforced_correct(client, monkeypatch):
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "secret")
    monkeypatch.setattr(webhooks.settings, "timestamp_tolerance_seconds", 300)

    envelope = {
        "event": "sms:received",
        "id": "evt-sig",
        "payload": {
            "phoneNumber": "04141234567",
            "message": "STATUS",
            "messageId": "m-sig",
        },
    }
    raw = json.dumps(envelope).encode("utf-8")
    ts = str(int(time.time()))
    sig = _signature(raw, ts, "secret")
    resp = await client.post(
        "/webhook/sms/events",
        content=raw,
        headers={"content-type": "application/json", "x-signature": sig, "x-timestamp": ts},
    )
    assert resp.status_code == 200


async def test_signature_enforced_wrong(client, monkeypatch):
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "secret")
    monkeypatch.setattr(webhooks.settings, "timestamp_tolerance_seconds", 300)

    envelope = {
        "event": "sms:received",
        "id": "evt-sig-wrong",
        "payload": {
            "phoneNumber": "04141234567",
            "message": "STATUS",
            "messageId": "m-sig-wrong",
        },
    }
    raw = json.dumps(envelope).encode("utf-8")
    ts = str(int(time.time()))
    sig = _signature(raw, ts, "wrong")
    resp = await client.post(
        "/webhook/sms/events",
        content=raw,
        headers={"content-type": "application/json", "x-signature": sig, "x-timestamp": ts},
    )
    assert resp.status_code == 401


def test_verify_signature_correct(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "secret")
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    monkeypatch.setattr(webhooks.settings, "timestamp_tolerance_seconds", 300)
    body = b'{"event":"sms:received"}'
    ts = str(int(time.time()))
    req = _Request({"x-signature": _signature(body, ts, "secret"), "x-timestamp": ts})
    assert webhooks._verify_sms_gate_signature(body, req) is None


def test_verify_signature_wrong_key(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "secret")
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    monkeypatch.setattr(webhooks.settings, "timestamp_tolerance_seconds", 300)
    body = b'{"event":"sms:received"}'
    ts = str(int(time.time()))
    req = _Request({"x-signature": _signature(body, ts, "wrong"), "x-timestamp": ts})
    assert webhooks._verify_sms_gate_signature(body, req) == "invalid signature"


def test_verify_signature_expired_timestamp(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "secret")
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    monkeypatch.setattr(webhooks.settings, "timestamp_tolerance_seconds", 300)
    body = b'{"event":"sms:received"}'
    ts = str(int(time.time()) - 1000)
    req = _Request({"x-signature": _signature(body, ts, "secret"), "x-timestamp": ts})
    assert webhooks._verify_sms_gate_signature(body, req) == "timestamp out of accepted range"


def test_verify_signature_missing_key_require_off(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "")
    monkeypatch.setattr(webhooks.settings, "require_signature", False)
    body = b'{"event":"sms:received"}'
    req = _Request({})
    assert webhooks._verify_sms_gate_signature(body, req) is None


def test_verify_signature_missing_key_require_on(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "webhook_signing_key", "")
    monkeypatch.setattr(webhooks.settings, "require_signature", True)
    body = b'{"event":"sms:received"}'
    req = _Request({})
    assert webhooks._verify_sms_gate_signature(body, req) is not None


def test_parse_body_json():
    assert webhooks.parse_body_bytes(b'{"a":1}', "application/json") == {"a": 1}


def test_parse_body_form_urlencoded():
    assert webhooks.parse_body_bytes(b"a=1&b=two", "application/x-www-form-urlencoded") == {
        "a": "1",
        "b": "two",
    }


def test_parse_body_garbage():
    assert webhooks.parse_body_bytes(b"not json or form", "text/plain") == {}


def test_parse_body_empty():
    assert webhooks.parse_body_bytes(b"", "application/json") == {}


def test_parse_body_invalid_json():
    assert webhooks.parse_body_bytes(b"{bad", "application/json") == {}

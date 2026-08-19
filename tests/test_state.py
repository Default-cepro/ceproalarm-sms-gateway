import asyncio

from src.api import state


async def test_handle_incoming_resolves_future(monkeypatch):
    fut = asyncio.get_event_loop().create_future()
    entry = {"id": "cmd1", "future": fut, "match_fn": lambda msg: "OK" in msg}
    monkeypatch.setitem(state.pending_commands, "4122571528", [entry])

    await state._handle_incoming_and_try_match(
        {"from": "04122571528", "message": "STATUS:OK", "raw": {}}
    )

    assert fut.done()
    result = fut.result()
    assert result["from"] == "04122571528"
    assert result["message"] == "STATUS:OK"


async def test_handle_incoming_wrong_phone_does_not_resolve(monkeypatch):
    fut = asyncio.get_event_loop().create_future()
    entry = {"id": "cmd1", "future": fut, "match_fn": lambda msg: True}
    monkeypatch.setitem(state.pending_commands, "4122571528", [entry])

    await state._handle_incoming_and_try_match(
        {"from": "04125551234", "message": "STATUS:OK", "raw": {}}
    )

    assert not fut.done()


async def test_handle_incoming_empty_phone_skipped(monkeypatch):
    fut = asyncio.get_event_loop().create_future()
    entry = {"id": "cmd1", "future": fut, "match_fn": lambda msg: True}
    monkeypatch.setitem(state.pending_commands, "4122571528", [entry])

    await state._handle_incoming_and_try_match({"from": "", "message": "STATUS:OK", "raw": {}})

    assert not fut.done()


def test_remember_delivery_first_true_second_false(monkeypatch):
    monkeypatch.setattr(state.settings, "max_tracked_deliveries", 100)
    assert state._remember_delivery("d1") is True
    assert state._remember_delivery("d1") is False


def test_remember_incoming_message_first_true_second_false(monkeypatch):
    monkeypatch.setattr(state.settings, "max_tracked_deliveries", 100)
    assert state._remember_incoming_message("m1") is True
    assert state._remember_incoming_message("m1") is False


def test_remember_status_event_first_true_second_false(monkeypatch):
    monkeypatch.setattr(state.settings, "max_tracked_deliveries", 100)
    assert state._remember_status_event("sms:delivered", "m1", "04122571528") is True
    assert state._remember_status_event("sms:delivered", "m1", "04122571528") is False


def test_remember_delivery_fifo_eviction(monkeypatch):
    state.recent_delivery_ids_order.clear()
    state.recent_delivery_ids_set.clear()
    monkeypatch.setattr(state.settings, "max_tracked_deliveries", 3)
    assert state._remember_delivery("a") is True
    assert state._remember_delivery("b") is True
    assert state._remember_delivery("c") is True
    # Re-inserting an existing id is a duplicate.
    assert state._remember_delivery("a") is False
    # Inserting a 4th id evicts the oldest (a).
    assert state._remember_delivery("d") is True
    assert state._remember_delivery("a") is True


def test_remember_incoming_message_fifo_eviction(monkeypatch):
    state.recent_incoming_message_ids_order.clear()
    state.recent_incoming_message_ids_set.clear()
    monkeypatch.setattr(state.settings, "max_tracked_deliveries", 3)
    assert state._remember_incoming_message("a") is True
    assert state._remember_incoming_message("b") is True
    assert state._remember_incoming_message("c") is True
    assert state._remember_incoming_message("a") is False
    assert state._remember_incoming_message("d") is True
    assert state._remember_incoming_message("a") is True

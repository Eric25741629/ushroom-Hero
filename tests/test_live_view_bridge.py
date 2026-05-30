"""Unit tests for the remote live-view CDP bridge pure logic.

Covers target selection, normalized-coordinate mapping, and the client-input ->
CDP Input.dispatch* translation. No real browser / CDP socket required.
"""
from __future__ import annotations

import json
import time

import pytest

from runtime_services import live_view_bridge as lvb


# ── find_game_page_target ──────────────────────────────────────────────

def _entries():
    return [
        {"type": "background_page", "url": "chrome-extension://x", "webSocketDebuggerUrl": "ws://x"},
        {"type": "page", "url": "https://other.example/", "webSocketDebuggerUrl": "ws://other"},
        {"type": "page", "url": "https://mushroomh5.acenetgame.com/", "webSocketDebuggerUrl": "ws://game"},
    ]


def test_find_target_prefers_host_match(monkeypatch):
    monkeypatch.setattr(lvb, "_http_get_json", lambda url, timeout=2.0: _entries())
    ws_url = lvb.find_game_page_target(9230, "mushroomh5.acenetgame.com", timeout_sec=1.0)
    assert ws_url == "ws://game"


def test_find_target_falls_back_to_first_page(monkeypatch):
    monkeypatch.setattr(lvb, "_http_get_json", lambda url, timeout=2.0: _entries())
    # Host with no match -> first page target (not the background_page).
    ws_url = lvb.find_game_page_target(9230, "no-such-host.test", timeout_sec=1.0)
    assert ws_url == "ws://other"


def test_find_target_returns_none_when_no_pages(monkeypatch):
    monkeypatch.setattr(lvb, "_http_get_json", lambda url, timeout=2.0: [])
    assert lvb.find_game_page_target(9230, "x", timeout_sec=0.2, poll_interval=0.05) is None


def test_find_target_returns_none_on_connection_error(monkeypatch):
    def _boom(url, timeout=2.0):
        raise OSError("connection refused")

    monkeypatch.setattr(lvb, "_http_get_json", _boom)
    assert lvb.find_game_page_target(9230, "x", timeout_sec=0.2, poll_interval=0.05) is None


# ── input translation ──────────────────────────────────────────────────

class _FakeCDP:
    def __init__(self):
        self.sent = []

    def send(self, raw):
        self.sent.append(json.loads(raw))


def _session():
    s = lvb.LiveViewSession(client_ws=object(), debug_port=9230, viewport_width=540, viewport_height=960)
    s._cdp = _FakeCDP()
    return s


def test_norm_to_css_uses_device_dimensions():
    s = _session()
    assert s._norm_to_css(0.5, 0.5) == (270.0, 480.0)
    # Clamped to [0, 1].
    assert s._norm_to_css(-1, 2) == (0.0, 960.0)


def test_mouse_down_dispatches_pressed_left():
    s = _session()
    s._dispatch_input({"type": "mouse", "action": "down", "nx": 0.5, "ny": 0.5})
    cmd = s._cdp.sent[-1]
    assert cmd["method"] == "Input.dispatchMouseEvent"
    p = cmd["params"]
    assert p["type"] == "mousePressed"
    assert p["button"] == "left"
    assert p["buttons"] == 1
    assert p["clickCount"] == 1
    assert (p["x"], p["y"]) == (270.0, 480.0)


def test_mouse_move_is_buttonless():
    s = _session()
    s._dispatch_input({"type": "mouse", "action": "move", "nx": 0.0, "ny": 0.0})
    p = s._cdp.sent[-1]["params"]
    assert p["type"] == "mouseMoved"
    assert p["button"] == "none"
    assert p["clickCount"] == 0


def test_mouse_up_dispatches_released():
    s = _session()
    s._dispatch_input({"type": "mouse", "action": "up", "nx": 1.0, "ny": 1.0})
    p = s._cdp.sent[-1]["params"]
    assert p["type"] == "mouseReleased"
    assert p["buttons"] == 0
    assert (p["x"], p["y"]) == (540.0, 960.0)


def test_unknown_mouse_action_sends_nothing():
    s = _session()
    s._dispatch_input({"type": "mouse", "action": "wiggle", "nx": 0.1, "ny": 0.1})
    assert s._cdp.sent == []


def test_key_down_includes_text():
    s = _session()
    s._dispatch_input({"type": "key", "action": "down", "key": "a", "code": "KeyA", "text": "a"})
    cmd = s._cdp.sent[-1]
    assert cmd["method"] == "Input.dispatchKeyEvent"
    assert cmd["params"]["type"] == "keyDown"
    assert cmd["params"]["text"] == "a"


def test_key_up_omits_text():
    s = _session()
    s._dispatch_input({"type": "key", "action": "up", "key": "a", "code": "KeyA", "text": "a"})
    p = s._cdp.sent[-1]["params"]
    assert p["type"] == "keyUp"
    assert "text" not in p


# ── idle auto-disconnect ────────────────────────────────────────────────

class _FakeClient:
    """Minimal flask-sock-like client socket. Idle by default (receive -> None)."""

    def __init__(self):
        self.sent = []
        self.closed = False

    def receive(self, timeout=1.0):
        return None  # simulate a 1 s timeout tick with no client input

    def send(self, raw):
        self.sent.append(raw)

    def close(self):
        self.closed = True


def _idle_session(idle_timeout_sec):
    fc = _FakeClient()
    s = lvb.LiveViewSession(client_ws=fc, debug_port=9230, idle_timeout_sec=idle_timeout_sec)
    return s, fc


def test_idle_timeout_auto_disconnects_and_notifies_client():
    s, fc = _idle_session(idle_timeout_sec=0.05)
    # Pretend the last input was long ago -> immediately idle on the first tick.
    s._last_input_ts = time.monotonic() - 100
    s._read_client_loop()
    assert s._stop.is_set()
    msgs = [json.loads(x) for x in fc.sent]
    assert any(m.get("type") == "closed" and m.get("reason") == "idle_timeout" for m in msgs)


def test_idle_disabled_does_not_auto_disconnect():
    s, fc = _idle_session(idle_timeout_sec=0)
    s._last_input_ts = time.monotonic() - 10_000

    # Break out of the otherwise-infinite idle loop after the first tick by making
    # receive() raise (treated as connection end). The loop must exit via THAT path,
    # not via an idle disconnect (idle is disabled), so no 'closed' frame is sent.
    calls = {"n": 0}

    def _recv(timeout=1.0):
        calls["n"] += 1
        raise RuntimeError("client gone")

    fc.receive = _recv
    s._read_client_loop()
    assert calls["n"] == 1
    assert all(json.loads(x).get("type") != "closed" for x in fc.sent)


def test_dispatch_input_resets_idle_timestamp():
    s = _session()
    s._last_input_ts = time.monotonic() - 50
    before = s._last_input_ts
    s._dispatch_input({"type": "mouse", "action": "move", "nx": 0.1, "ny": 0.1})
    assert s._last_input_ts > before

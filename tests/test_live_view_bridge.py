"""Unit tests for the remote live-view CDP bridge pure logic.

Covers target selection, normalized-coordinate mapping, and the client-input ->
CDP Input.dispatch* translation. No real browser / CDP socket required.
"""
from __future__ import annotations

import json

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

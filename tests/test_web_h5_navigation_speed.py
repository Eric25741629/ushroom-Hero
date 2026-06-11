from __future__ import annotations

import pytest

device_wrapper = pytest.importorskip("device_wrapper")


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls = []
        self.reload_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append((url, dict(kwargs)))
        return True

    def reload(self, **kwargs):
        self.reload_calls.append(dict(kwargs))
        return True


def _make_device(monkeypatch, cfg):
    monkeypatch.setattr(device_wrapper.PlaywrightGameDevice, "_start", lambda self: None)
    dev = device_wrapper.PlaywrightGameDevice("emu-web-speed", cfg=cfg)
    dev._page = _FakePage()
    return dev


def test_open_game_url_does_not_force_reload_by_default(monkeypatch):
    dev = _make_device(monkeypatch, {"web_url": "https://example.invalid/game"})

    assert dev._open_game_url() is True

    assert len(dev._page.goto_calls) == 1
    assert dev._page.goto_calls[0][1]["wait_until"] == "domcontentloaded"
    assert dev._page.reload_calls == []


def test_open_game_url_can_force_reload_when_configured(monkeypatch):
    dev = _make_device(
        monkeypatch,
        {
            "web_url": "https://example.invalid/game",
            "web_reload_after_goto": True,
        },
    )

    assert dev._open_game_url() is True

    assert len(dev._page.goto_calls) == 1
    assert len(dev._page.reload_calls) == 1
    assert dev._page.reload_calls[0]["wait_until"] == "domcontentloaded"

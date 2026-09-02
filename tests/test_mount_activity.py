"""坐騎活動型別探測測試。"""
from __future__ import annotations

import pytest

from ws_token import mount_activity
from ws_token.client import WSError, WSTimeoutError


def test_find_active_mount_event_returns_server_type(monkeypatch):
    calls = []

    def fake_read(client, act_type, *, timeout=None):
        calls.append((act_type, timeout))
        return {"open": act_type == 266, "act_type": act_type}

    monkeypatch.setattr(mount_activity.relic_sprint, "read_sprint", fake_read)

    assert mount_activity.find_active_act_type(object(), timeout=0.5) == 266
    assert calls == [(9, 0.5), (266, 0.5)]


def test_find_active_mount_event_returns_none_when_closed(monkeypatch):
    monkeypatch.setattr(
        mount_activity.relic_sprint,
        "read_sprint",
        lambda *args, **kwargs: {"open": False},
    )

    assert mount_activity.find_active_act_type(object()) is None


@pytest.mark.parametrize("error", [WSError("closed"), WSTimeoutError("timeout")])
def test_find_active_mount_event_fails_closed_on_probe_error(monkeypatch, error):
    import pytest

    monkeypatch.setattr(
        mount_activity.relic_sprint,
        "read_sprint",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    assert mount_activity.find_active_act_type(object()) is None

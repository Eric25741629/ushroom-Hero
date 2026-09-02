"""坐騎衝刺純 WS 協定、排程與成功記錄測試。"""
from __future__ import annotations

import datetime

import pytest

from ws_token import codec, mount_sprint
from ws_token.client import WSError


_TPE = datetime.timezone(datetime.timedelta(hours=8))


class _FakeClient:
    def __init__(self, reply_cmd, reply_body):
        self.reply_cmd = reply_cmd
        self.reply_body = reply_body
        self.calls = []

    def call_for(self, cmd, body, *, expect_cmds):
        self.calls.append((cmd, body, expect_cmds))
        return self.reply_cmd, self.reply_body


def _tue():
    return datetime.datetime(2026, 7, 7, 10, 0, tzinfo=_TPE)


def test_build_level_up_body_matches_live_custom_quantity_frame():
    assert mount_sprint.build_level_up_body(3200) == bytes(
        (0x08, 0x00, 0x10, 0x80, 0x19)
    )


def test_run_sends_mount_level_up_and_records_only_after_success(monkeypatch):
    client = _FakeClient(
        mount_sprint.CMD_MOUNT_LEVEL_UP,
        codec.pb_uint(1, 578297),
    )
    monkeypatch.setattr(mount_sprint.json_manager, "return_time", lambda *a, **k: None)
    monkeypatch.setattr(mount_sprint.mount_activity, "find_active_act_type", lambda *a, **k: 266)
    monkeypatch.setattr(
        mount_sprint.json_manager,
        "should_execute_cycle",
        lambda *a, **k: (True, True),
    )
    recorded = []
    monkeypatch.setattr(
        mount_sprint.json_manager,
        "time_recording",
        lambda ip, name="": recorded.append((ip, name)),
    )

    result = mount_sprint.run(client, "dev", quantity=3200, now=_tue())

    assert result == {"quantity": 3200, "act_type": 266, "exp": 578297, "recorded": True}
    assert client.calls == [(
        mount_sprint.CMD_MOUNT_LEVEL_UP,
        bytes((0x08, 0x00, 0x10, 0x80, 0x19)),
        (mount_sprint.CMD_MOUNT_LEVEL_UP, mount_sprint.CMD_ERROR),
    )]
    assert recorded == [("dev", "衝刺-發條")]


def test_run_does_not_send_when_not_due(monkeypatch):
    monkeypatch.setattr(mount_sprint.mount_activity, "find_active_act_type", lambda *a, **k: None)
    client = _FakeClient(mount_sprint.CMD_MOUNT_LEVEL_UP, b"")
    monkeypatch.setattr(mount_sprint, "is_due", lambda *a, **k: False)

    assert mount_sprint.run(client, "dev", quantity=1, now=_tue()) == {
        "skipped": "not due"
    }
    assert client.calls == []


def test_run_does_not_send_when_server_mount_event_is_closed(monkeypatch):
    monkeypatch.setattr(mount_sprint.mount_activity, "find_active_act_type", lambda *a, **k: None)
    monkeypatch.setattr(mount_sprint.json_manager, "return_time", lambda *a, **k: None)
    monkeypatch.setattr(
        mount_sprint.json_manager,
        "should_execute_cycle",
        lambda *a, **k: (True, True),
    )
    client = _FakeClient(mount_sprint.CMD_MOUNT_LEVEL_UP, b"")

    result = mount_sprint.run(client, "dev", quantity=1, now=_tue())

    assert result == {"skipped": "mount sprint: no active server event"}
    assert client.calls == []

def test_server_error_does_not_record(monkeypatch):
    monkeypatch.setattr(mount_sprint.mount_activity, "find_active_act_type", lambda *a, **k: 266)
    client = _FakeClient(mount_sprint.CMD_ERROR, codec.pb_uint(1, 25))
    monkeypatch.setattr(mount_sprint.json_manager, "return_time", lambda *a, **k: None)
    monkeypatch.setattr(
        mount_sprint.json_manager,
        "should_execute_cycle",
        lambda *a, **k: (True, True),
    )
    recorded = []
    monkeypatch.setattr(
        mount_sprint.json_manager,
        "time_recording",
        lambda ip, name="": recorded.append((ip, name)),
    )

    with pytest.raises(WSError, match="error_code=25"):
        mount_sprint.run(client, "dev", quantity=1, now=_tue())
    assert recorded == []


def test_ws_phase_forwards_mount_sprint_settings(monkeypatch):
    from game_actions import ws_phase

    captured = {}

    def fake_run_device(ip, **kwargs):
        captured["ip"] = ip
        captured.update(kwargs)
        return object()

    import ws_token.runner as runner
    monkeypatch.setattr(runner, "run_device", fake_run_device)

    ws_phase._run_device(
        "dev",
        {"mount_sprint_enabled": True, "mount_sprint_quantity": 1234},
    )

    assert captured["ip"] == "dev"
    assert captured["mount_sprint_enabled"] is True
    assert captured["mount_sprint_quantity"] == 1234
    assert ws_phase.WS_TO_PIPELINE_SKIPS["mount_sprint"] == ("坐騎強化",)

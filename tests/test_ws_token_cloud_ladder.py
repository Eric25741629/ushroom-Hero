"""雲纏天梯純 WS 協定與每週挑戰測試。"""
from __future__ import annotations

import datetime

from ws_token import cloud_ladder, codec


def _dc_info(*, now_level: int, max_level: int, teammate_id: int = 3) -> bytes:
    return (
        codec.pb_uint(1, now_level)
        + codec.pb_uint(2, max_level)
        + codec.pb_uint(6, 1000)
        + codec.pb_uint(7, 1000)
        + codec.pb_uint(8, teammate_id)
        + codec.pb_uint(9, 800)
        + codec.pb_uint(10, 800)
    )


def _level_info(*positions: tuple[tuple[int, int], ...]) -> bytes:
    body = b"".join(
        codec.pb_msg(1, codec.pb_uint(1, pos) + codec.pb_uint(2, role))
        for pos, role in positions
    )
    return body + codec.pb_uint(2, 1)


class _ScriptClient:
    def __init__(self):
        self.calls: list[tuple[int, bytes, tuple[int, ...]]] = []
        self.level = 142

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.calls.append((cmd, bytes(body), tuple(expect_cmds)))
        if cmd == cloud_ladder.CMD_ENTRANCE_INFO:
            return cmd, codec.pb_uint(1, 1)
        if cmd == cloud_ladder.CMD_DC_INFO:
            return cmd, _dc_info(
                now_level=self.level, max_level=143, teammate_id=3)
        if cmd == cloud_ladder.CMD_LEVEL_INFO:
            return cmd, _level_info((1, 1), (5, 2))
        if cmd == cloud_ladder.CMD_BATTLE_START:
            fields = codec.walk_dict(body)
            assert fields[1] == cloud_ladder.TYPE_DOUBLE_LADDER
            assert fields[2] == self.level
            return cmd, (
                codec.pb_uint(1, 0)
                + codec.pb_uint(2, cloud_ladder.TYPE_DOUBLE_LADDER)
                + codec.pb_uint(3, self.level)
                + codec.pb_uint(5, 9945)
            )
        if cmd == cloud_ladder.CMD_BATTLE_RESULT:
            fields = codec.walk_dict(body)
            assert fields[1] == cloud_ladder.TYPE_DOUBLE_LADDER
            assert fields[2] == self.level
            assert fields[3] == 0
            assert fields[4] == 0
            self.level += 1
            return cloud_ladder.CMD_DUNGEON_RESULT, (
                codec.pb_uint(1, 0)
                + codec.pb_uint(2, self.level - 1)
                + codec.pb_uint(3, cloud_ladder.TYPE_DOUBLE_LADDER)
                + codec.pb_uint(4, 0)
            )
        raise AssertionError(f"unexpected cmd {cmd}")


def test_run_weekly_finishes_every_remaining_level(monkeypatch):
    client = _ScriptClient()
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(cloud_ladder, "is_due", lambda *a, **k: (True, "due"))
    monkeypatch.setattr(
        cloud_ladder.json_manager,
        "time_recording",
        lambda device, name="": recorded.append((device, name)),
    )

    out = cloud_ladder.run_weekly(
        client,
        "emulator-5556",
        now=datetime.datetime(2026, 7, 30, 12, 0),
    )

    assert out["completed"] is True
    assert out["fights"] == 2
    assert out["start_level"] == 142
    assert out["final_level"] == 144
    assert recorded == [("emulator-5556", "cloud_fighting_weekly")]
    sent = [cmd for cmd, _body, _expect in client.calls]
    assert sent.count(cloud_ladder.CMD_BATTLE_START) == 2
    assert sent.count(cloud_ladder.CMD_BATTLE_RESULT) == 2


def test_ensure_safe_positions_moves_roles_to_one_and_five():
    calls: list[tuple[int, bytes]] = []

    class Client:
        def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
            calls.append((cmd, bytes(body)))
            if cmd == cloud_ladder.CMD_LEVEL_INFO:
                return cmd, _level_info((3, 1), (5, 2))
            if cmd == cloud_ladder.CMD_CHANGE_POS:
                return cmd, codec.pb_uint(1, 0)
            raise AssertionError(cmd)

    out = cloud_ladder.ensure_safe_positions(Client())

    assert out == ((1, 1), (5, 2))
    changed = codec.walk(calls[-1][1])
    pairs = [
        codec.walk_dict(bytes(value))
        for field, value in changed
        if field == 1
    ]
    assert pairs == [{1: 1, 2: 1}, {1: 5, 2: 2}]


def test_run_weekly_skips_when_not_due(monkeypatch):
    monkeypatch.setattr(
        cloud_ladder, "is_due", lambda *a, **k: (False, "monday_before_03"))
    out = cloud_ladder.run_weekly(object(), "dev")
    assert out == {"skipped": "monday_before_03"}


def test_old_weekly_marker_does_not_hide_incomplete_server_state(monkeypatch):
    client = _ScriptClient()
    monkeypatch.setattr(
        cloud_ladder, "is_due", lambda *a, **k: (False, "already_this_week"))
    monkeypatch.setattr(
        cloud_ladder.json_manager, "time_recording", lambda *a, **k: None)

    out = cloud_ladder.run_weekly(client, "emulator-5556")

    assert out["completed"] is True
    assert out["fights"] == 2


def test_activity_closed_error_is_safe_skip(monkeypatch):
    monkeypatch.setattr(cloud_ladder, "is_due", lambda *a, **k: (True, "due"))
    recorded = []
    monkeypatch.setattr(
        cloud_ladder.json_manager,
        "time_recording",
        lambda device, name="": recorded.append((device, name)),
    )

    class Client:
        def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
            assert cmd == cloud_ladder.CMD_ENTRANCE_INFO
            return cloud_ladder.CMD_ERROR, codec.pb_uint(
                1, cloud_ladder.ERROR_ACTIVITY_CLOSED)

    assert cloud_ladder.run_weekly(Client(), "dev") == {
        "completed": False,
        "activity_closed": True,
    }
    assert recorded == [("dev", "cloud_fighting_weekly")]

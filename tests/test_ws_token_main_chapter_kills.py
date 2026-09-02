from __future__ import annotations

from datetime import datetime

from ws_token import codec
from ws_token import main_chapter_kills as kills
from ws_token import state as ws_state


class _Runtime:
    def __init__(self, *, should_abort=None):
        self.parts = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def units(self, part_id):
        self.parts.append(part_id)
        return [(9001, 1), (9002, 5)]


class _Client:
    def __init__(self):
        self.connected = False
        self.closed = False
        self.calls = []

    def connect(self):
        self.connected = True
        return {"code": 0}

    def call(self, cmd, body):
        self.calls.append((cmd, body))
        if cmd == kills.CMD_INFO:
            return codec.pb_uint(1, 33451)
        if cmd == kills.CMD_ENTER:
            return codec.pb_uint(1, 33452)
        if cmd == kills.CMD_RESULT:
            return codec.pb_uint(1, 0) + codec.pb_uint(2, 33453)
        return b""

    def close(self):
        self.closed = True


class _OfflineClient(_Client):
    def connect(self):
        raise RuntimeError("device not online")


def test_target_is_3000_only_on_friday():
    assert kills.target_for_day(datetime(2026, 7, 30)) == 150
    assert kills.target_for_day(datetime(2026, 7, 31)) == 3000
    assert kills.target_for_day(datetime(2026, 8, 1)) == 150
    assert kills.target_for_day(datetime(2026, 8, 2)) == 150


def test_run_daily_uses_latest_part_and_persists_resume(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(kills, "DAILY_TARGET", 3)
    client = _Client()
    updates = []

    result = kills.run_daily(
        "phone",
        interval_sec=0,
        persist_every=1,
        now=datetime(2026, 7, 30, 12),
        state_dir=tmp_path,
        runtime_factory=_Runtime,
        client_factory=lambda: client,
        progress=lambda sent, target: updates.append((sent, target)),
    )

    assert result == {"sent": 3, "target": 3, "friday": False}
    assert client.connected is True
    assert client.closed is True
    assert [cmd for cmd, _ in client.calls].count(kills.CMD_KILL) == 3
    kill_body = next(body for cmd, body in client.calls if cmd == kills.CMD_KILL)
    assert codec.walk_dict(kill_body)[1] == 33452
    assert updates == [(1, 3), (2, 3), (3, 3)]

    saved = ws_state.load_state("phone", state_dir=tmp_path)
    assert saved["main_chapter_kills"] == {
        "date": "2026-07-30",
        "target": 3,
        "sent": 3,
        "completed": True,
    }

    second = kills.run_daily(
        "phone",
        now=datetime(2026, 7, 30, 18),
        state_dir=tmp_path,
        runtime_factory=lambda **_kw: (_ for _ in ()).throw(
            AssertionError("runtime must not start")
        ),
        client_factory=lambda: (_ for _ in ()).throw(
            AssertionError("client must not start")
        ),
    )
    assert second == {"skipped": "today complete", "sent": 3, "target": 3}


def test_new_date_resets_sent_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(kills, "DAILY_TARGET", 1)
    ws_state.save_state(
        "phone",
        {
            "main_chapter_kills": {
                "date": "2026-07-29",
                "target": 150,
                "sent": 150,
                "completed": True,
            }
        },
        state_dir=tmp_path,
    )
    client = _Client()

    result = kills.run_daily(
        "phone",
        interval_sec=0,
        now=datetime(2026, 7, 30),
        state_dir=tmp_path,
        runtime_factory=_Runtime,
        client_factory=lambda: client,
    )

    assert result["sent"] == 1
    assert [cmd for cmd, _ in client.calls].count(kills.CMD_KILL) == 1


def test_offline_client_fails_before_b_runtime_starts(tmp_path):
    runtime_started = False

    def runtime_factory(**_kwargs):
        nonlocal runtime_started
        runtime_started = True
        raise AssertionError("B runtime must not start when A client is offline")

    client = _OfflineClient()
    try:
        kills.run_daily(
            "phone",
            now=datetime(2026, 7, 30, 12),
            state_dir=tmp_path,
            runtime_factory=runtime_factory,
            client_factory=lambda: client,
        )
    except RuntimeError as exc:
        assert "not online" in str(exc)
    else:
        raise AssertionError("offline client error should propagate")
    assert runtime_started is False
    assert client.closed is True

"""Tests for 龍骸 SOS 純-WS 自動協助 (ws_token/dragon_sos.py).

協議與基礎設施都已 live-verified（見 tasks/todo.md 2026-06-26）；這裡用 fake client /
fake session 驗純邏輯：清單解析、只救 pending、重讀回報、ws_session 接線與還原。
"""
from __future__ import annotations

import datetime

import pytest

from ws_token import codec
from ws_token import dragon_sos
from game_actions import dragon_realm_scheduler as sched


# --- helpers: build a help_event_list_s2c body ------------------------------

def _event(id_: int, event_id: int, role_id: int, status: int) -> bytes:
    return (
        codec.pb_uint(1, id_)
        + codec.pb_uint(2, event_id)
        + codec.pb_uint(3, role_id)
        + codec.pb_uint(4, status)
    )


def _help_list(events: list[bytes], help_hp: int) -> bytes:
    body = b""
    for ev in events:
        body += codec.pb_msg(1, ev)  # repeated p_dragon_realm_event (field 1)
    body += codec.pb_uint(2, help_hp)  # help_hp (field 2)
    return body


class _FakeClient:
    """Returns successive help-list bodies on call(0x4F15); records sends."""

    def __init__(self, list_bodies: list[bytes]):
        self._lists = list(list_bodies)
        self.sent: list[tuple[int, bytes]] = []

    def call(self, cmd: int, body: bytes = b"", *, expect_cmd=None, timeout=None) -> bytes:
        assert cmd == dragon_sos.CMD_HELP_LIST
        return self._lists.pop(0)

    def send(self, cmd: int, body: bytes = b"") -> None:
        self.sent.append((cmd, body))


# --- read_help_list ---------------------------------------------------------

def test_read_help_list_parses_multiple_events_and_help_hp():
    body = _help_list(
        [_event(100, 13, 999, 0), _event(101, 5, 888, 1)], help_hp=2
    )
    events, help_hp = dragon_sos.read_help_list(_FakeClient([body]))

    assert help_hp == 2
    assert events == [
        {"id": 100, "event_id": 13, "role_id": 999, "status": 0},
        {"id": 101, "event_id": 5, "role_id": 888, "status": 1},
    ]


# --- rescue_pending ---------------------------------------------------------

def test_rescue_pending_only_helps_pending_events():
    before = _help_list(
        [_event(100, 13, 999, 0), _event(101, 5, 888, 1)], help_hp=2
    )
    after = _help_list([], help_hp=1)  # cleared after help
    client = _FakeClient([before, after])

    result = dragon_sos.rescue_pending(client)

    # only the status==0 event (role 999, trap 13) gets provide_help
    assert client.sent == [
        (dragon_sos.CMD_PROVIDE_HELP, codec.pb_uint(1, 999) + codec.pb_uint(2, 13))
    ]
    assert result["attempted"] == 1
    assert result["remaining"] == 0
    assert result["help_hp_before"] == 2
    assert result["help_hp_after"] == 1
    assert result["targets"][0]["role_id"] == 999


def test_rescue_pending_also_helps_attack_events_with_non_one_status():
    """進攻求助的狀態可能是 2；前端規則是 status != 1 才可協助。"""
    before = _help_list(
        [_event(200, 7, 777, 2), _event(201, 5, 888, 1)], help_hp=2
    )
    after = _help_list([], help_hp=1)
    client = _FakeClient([before, after])

    result = dragon_sos.rescue_pending(client)

    assert client.sent == [
        (dragon_sos.CMD_PROVIDE_HELP, codec.pb_uint(1, 777) + codec.pb_uint(2, 7))
    ]
    assert result["attempted"] == 1
    assert result["targets"][0]["event_id"] == 7


def test_rescue_pending_no_pending_sends_nothing():
    body = _help_list([_event(101, 5, 888, 1)], help_hp=2)  # only a done event
    client = _FakeClient([body, body])

    result = dragon_sos.rescue_pending(client)

    assert client.sent == []
    assert result["attempted"] == 0
    assert result["remaining"] == 0


def test_rescue_pending_empty_list_sends_nothing():
    body = _help_list([], help_hp=0)
    client = _FakeClient([body, body])

    result = dragon_sos.rescue_pending(client)

    assert client.sent == []
    assert result["attempted"] == 0


def test_claim_help_rewards_only_claims_own_completed_event():
    before = _help_list(
        [_event(200, 7, 777, 1), _event(201, 8, 888, 1), _event(202, 9, 777, 0)],
        help_hp=1,
    )
    after = _help_list(
        [_event(201, 8, 888, 1), _event(202, 9, 777, 0)], help_hp=1
    )
    client = _FakeClient([before, after])

    result = dragon_sos.claim_help_rewards(client, my_role_id=777)

    assert client.sent == [
        (dragon_sos.CMD_RECEIVE_HELP, codec.pb_uint(1, 7))
    ]
    assert result["claimed"] == 1
    assert result["remaining"] == 0


# --- rescue_via_ws (ws_session wiring) --------------------------------------

class _FakeSession:
    def __init__(self, client, *, preexisting=False, ensure_ok=True):
        self._client = client
        self._preexisting = preexisting
        self._ensure_ok = ensure_ok
        self.ensured = False
        self.disconnected = False

    def get_client(self, ip):
        # before ensure: None unless preexisting; after ensure: the client
        if self._preexisting or self.ensured:
            return self._client
        return None

    def ensure(self, ip):
        self.ensured = True
        return {"status": "ok"} if self._ensure_ok else {"status": "error", "message": "boom"}

    def disconnect(self, ip):
        self.disconnected = True
        return {"status": "ok"}


def test_rescue_via_ws_disconnects_when_it_created_the_session():
    before = _help_list([_event(100, 13, 999, 0)], help_hp=2)
    after = _help_list([], help_hp=1)
    client = _FakeClient([before, after])
    session = _FakeSession(client, preexisting=False)

    result = dragon_sos.rescue_via_ws("emulator-5554", session=session)

    assert result["status"] == "ok"
    assert result["attempted"] == 1
    assert session.ensured is True
    assert session.disconnected is True  # SOS created it -> SOS cleans it up


def test_rescue_via_ws_keeps_preexisting_session():
    before = _help_list([_event(100, 13, 999, 0)], help_hp=2)
    after = _help_list([], help_hp=1)
    client = _FakeClient([before, after])
    session = _FakeSession(client, preexisting=True)

    result = dragon_sos.rescue_via_ws("emulator-5554", session=session)

    assert result["status"] == "ok"
    assert session.disconnected is False  # don't kill a session we didn't open


def test_rescue_via_ws_returns_error_when_ensure_fails():
    session = _FakeSession(_FakeClient([]), ensure_ok=False)

    result = dragon_sos.rescue_via_ws("emulator-5554", session=session)

    assert result["status"] == "error"
    assert "boom" in result["message"]
    assert session.disconnected is False


# --- is_dragon_open ---------------------------------------------------------

def test_is_dragon_open_true_in_window_on_dragon_week():
    # 2026-06-24 is a Wed inside the anchor dragon week (anchor Mon 2026-06-22)
    now = datetime.datetime(2026, 6, 24, 10, 0)
    assert sched.is_dragon_open(now) is True


def test_is_dragon_open_false_before_open_hour():
    now = datetime.datetime(2026, 6, 24, 9, 59)
    assert sched.is_dragon_open(now) is False


def test_is_dragon_open_false_on_weekend():
    now = datetime.datetime(2026, 6, 27, 12, 0)  # Sat
    assert sched.is_dragon_open(now) is False


def test_is_dragon_open_false_off_cycle_week():
    # 2026-06-17 is a Wed but the week before the anchor week (not a dragon week)
    now = datetime.datetime(2026, 6, 17, 12, 0)
    assert sched.is_dragon_open(now) is False

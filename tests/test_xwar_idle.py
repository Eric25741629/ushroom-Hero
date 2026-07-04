"""跨服戰 放置獎勵 (cross_war idle reward) pure-WS auto-claim — unit tests.

Covers the two parsers (act_list window + claim reply) and the cadence gate
(``claim_if_due``): 8h interval, act_list open/closed, claim ok/error, dormant
no-response, and ws_state ledger persistence. No live socket — a FakeClient
returns canned protobuf built with the real ``ws_token.codec`` encoders, and the
act_list field mapping is anchored to a live capture (type=33 entry, 2026-06-28).
"""
from __future__ import annotations

import datetime

import pytest

from ws_token import codec, xwar_idle
from ws_token import state as ws_state
from ws_token.client import WSTimeoutError


# --- helpers: build protobuf bodies with the real wire encoders --------------

def _act_entry(type_: int, state: int, start: int, end: int, round_: int = 1) -> bytes:
    """One p_activity submessage: type#2, round#3, state#5, start#6, end#7."""
    return (codec.pb_uint(2, type_) + codec.pb_uint(3, round_)
            + codec.pb_uint(5, state) + codec.pb_uint(6, start)
            + codec.pb_uint(7, end))


def _act_list(*entries: bytes) -> bytes:
    """act_list_s2c: repeated p_activity on field 1."""
    return b"".join(codec.pb_msg(1, e) for e in entries)


class FakeClient:
    """Minimal stand-in for WSGameClient.{call,call_for} the module uses."""

    def __init__(self, *, act_body: bytes | None = None, act_exc: Exception | None = None,
                 claim_reply: tuple[int, bytes] | None = None,
                 claim_exc: Exception | None = None) -> None:
        self.act_body = act_body
        self.act_exc = act_exc
        self.claim_reply = claim_reply
        self.claim_exc = claim_exc
        self.calls: list[tuple[str, int]] = []

    def call(self, cmd: int, body: bytes = b"", *, timeout=None, expect_cmd=None) -> bytes:
        self.calls.append(("call", cmd))
        if cmd == xwar_idle.CMD_ACT_LIST:
            if self.act_exc:
                raise self.act_exc
            return self.act_body or b""
        raise AssertionError(f"unexpected call cmd=0x{cmd:04x}")

    def call_for(self, cmd: int, body: bytes = b"", *, expect_cmds, timeout=None):
        self.calls.append(("call_for", cmd))
        if cmd == xwar_idle.CMD_CLAIM:
            if self.claim_exc:
                raise self.claim_exc
            return self.claim_reply
        raise AssertionError(f"unexpected call_for cmd=0x{cmd:04x}")


# --- parse_act_list ----------------------------------------------------------

def test_parse_act_list_open_uses_live_field_mapping():
    # Anchored to the 2026-06-28 live capture: type33 entry f2=33,f5=2,f6/f7 ts.
    body = _act_list(
        _act_entry(10, 2, 1, 2),                       # some other open activity
        _act_entry(33, 2, 1782525600, 1782660599, 133),  # cross-war, Open
    )
    w = xwar_idle.parse_act_list(body)
    assert w.found is True
    assert w.is_open is True
    assert w.state == xwar_idle.STATE_OPEN
    assert w.start_ts == 1782525600
    assert w.end_ts == 1782660599


def test_parse_act_list_preview_is_not_open():
    w = xwar_idle.parse_act_list(_act_list(_act_entry(33, 1, 100, 200)))  # state=1 Preview
    assert w.found is True
    assert w.is_open is False


def test_parse_act_list_missing_cross_war():
    w = xwar_idle.parse_act_list(_act_list(_act_entry(34, 2, 100, 200)))
    assert w.found is False
    assert w.is_open is False


# --- parse_claim -------------------------------------------------------------

def test_parse_claim_success_carries_new_last_time():
    r = xwar_idle.parse_claim(xwar_idle.CMD_CLAIM, codec.pb_uint(1, 1782584002))
    assert r.ok is True
    assert r.new_last_time == 1782584002


def test_parse_claim_error_carries_code():
    r = xwar_idle.parse_claim(xwar_idle.CMD_ERROR, codec.pb_uint(1, 173))
    assert r.ok is False
    assert r.error_code == 173


def test_parse_claim_unexpected_cmd_is_failure():
    r = xwar_idle.parse_claim(0x9999, b"")
    assert r.ok is False


# --- claim_if_due gate -------------------------------------------------------

NOW = datetime.datetime(2026, 6, 27, 12, 0, 0)
DEVICE = "emulator-5560"


def _seed(tmp_path, **rec):
    ws_state.save_state(DEVICE, {"xwar_idle": rec}, state_dir=tmp_path)


def _load(tmp_path) -> dict:
    return ws_state.load_state(DEVICE, state_dir=tmp_path).get("xwar_idle") or {}


def test_within_interval_skips_without_any_ws_call(tmp_path):
    _seed(tmp_path, last_attempt_ts=(NOW.timestamp() - 3600))  # 1h ago < 8h
    client = FakeClient()
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is False
    assert client.calls == []  # throttled before touching the socket


def test_open_event_claims_and_persists_success(tmp_path):
    _seed(tmp_path, last_attempt_ts=(NOW.timestamp() - 9 * 3600))  # 9h ago >= 8h
    client = FakeClient(
        act_body=_act_list(_act_entry(33, 2, 1, 9999999999)),
        claim_reply=(xwar_idle.CMD_CLAIM, codec.pb_uint(1, 1782584002)),
    )
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is True
    assert out["ok"] is True
    assert out["new_last_time"] == 1782584002
    rec = _load(tmp_path)
    assert rec["last_success_ts"] == NOW.timestamp()
    assert rec["last_new_time"] == 1782584002


def test_closed_event_skips_claim_but_records_attempt(tmp_path):
    _seed(tmp_path, last_attempt_ts=(NOW.timestamp() - 9 * 3600))
    client = FakeClient(act_body=_act_list(_act_entry(33, 1, 1, 2)))  # Preview, not open
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is False
    assert ("call_for", xwar_idle.CMD_CLAIM) not in client.calls  # never tried to claim
    rec = _load(tmp_path)
    assert rec["last_attempt_ts"] == NOW.timestamp()  # attempt throttle advanced
    assert "last_success_ts" not in rec


def test_act_list_timeout_is_benign_skip(tmp_path):
    _seed(tmp_path, last_attempt_ts=(NOW.timestamp() - 9 * 3600))
    client = FakeClient(act_exc=WSTimeoutError("dormant"))
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is False
    assert _load(tmp_path)["last_attempt_ts"] == NOW.timestamp()


def test_claim_rejected_0x0201_does_not_record_success(tmp_path):
    _seed(tmp_path, last_attempt_ts=(NOW.timestamp() - 9 * 3600))
    client = FakeClient(
        act_body=_act_list(_act_entry(33, 2, 1, 9999999999)),
        claim_reply=(xwar_idle.CMD_ERROR, codec.pb_uint(1, 159)),
    )
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is True
    assert out["ok"] is False
    assert out["error_code"] == 159
    assert "last_success_ts" not in _load(tmp_path)


def test_first_run_ever_proceeds(tmp_path):
    # no prior ledger -> last_attempt defaults to 0 -> due immediately
    client = FakeClient(
        act_body=_act_list(_act_entry(33, 2, 1, 9999999999)),
        claim_reply=(xwar_idle.CMD_CLAIM, codec.pb_uint(1, 1782584002)),
    )
    out = xwar_idle.claim_if_due(client, device=DEVICE, state_dir=tmp_path, now=NOW)
    assert out["claimed_run"] is True

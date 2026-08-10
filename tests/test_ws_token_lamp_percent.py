"""Tests for ws_token.lamp percent / min-keep + progress (Phase A).

Covers the new pure-function helpers (round_to_nearest_20, extract_lamp_count,
compute_lamp_target) and the open_lamp() extensions that add:
  - lamp_percent / lamp_min_keep target computation,
  - live `remaining` tracking from 0x0402 (evt 1001006 + snapshot),
  - on_progress(opened, target) per-batch callback,
  - target / initial_count / remaining in the returned dict.

The open_lamp tests drive a FakeTransport responder so that opening a batch
(0x0509) ALSO returns a 0x0402 consume push; that push has no waiter so the
client routes it to the installed push handler, exactly like the live server.
We use empty equip_info (no worn items) so set-map derivation never touches the
OCR path, and withhold 0x0504 drop details (each uid -> "left"), so dry_run
runs need no OCRParser/SkillEvaluator decision work.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ws_token.lamp transitively imports opengold_v2 -> cv2; stub it like the
# sibling lamp test so the import never needs the real OpenCV runtime.
sys.modules.setdefault("cv2", types.SimpleNamespace())

from ws_token import codec, lamp  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)

CMD_EQUIP_INFO = 0x0501
CMD_OPEN_ALL = 0x0509
CMD_TAB_INFO = 0x0510
CMD_INVENTORY_PUSH = 0x0402

EVT_LAMP_CONSUME = 1001006
EVT_SNAPSHOT = 9700002


# --- 0x0402 body builders (mirror EQUIPMENT_SCHEMA §9-10) -------------------

def _inv_item(item_id: int, qty: int, uid: int = 0) -> bytes:
    # f2 sub: {item_id#1, uid#2(optional), qty#3 = 當前剩餘}
    sub = codec.pb_uint(1, item_id)
    if uid:
        sub += codec.pb_uint(2, uid)
    sub += codec.pb_uint(3, qty)
    return sub


def _inv_push(evt_type: int, items) -> bytes:
    # 0x0402 body: {evt_type#1, item#2(repeated): sub}
    return codec.pb_uint(1, evt_type) + b"".join(
        codec.pb_msg(2, it) for it in items)


def _open_all_s2c(uids) -> bytes:
    return b"".join(codec.pb_uint(1, u) for u in uids)


# --- round_to_nearest_20 ----------------------------------------------------

def test_round_to_nearest_20_boundaries():
    assert lamp.round_to_nearest_20(10001) == 10000
    assert lamp.round_to_nearest_20(10011) == 10020
    assert lamp.round_to_nearest_20(19) == 20      # >=10 rounds up to 20
    assert lamp.round_to_nearest_20(9) == 0        # <10 rounds down to 0
    assert lamp.round_to_nearest_20(10) == 20      # exact half rounds to 20
    assert lamp.round_to_nearest_20(0) == 0


def test_round_to_nearest_20_negative_clamps_to_zero():
    assert lamp.round_to_nearest_20(-5) == 0
    assert lamp.round_to_nearest_20(-100) == 0


# --- extract_lamp_count -----------------------------------------------------

def test_extract_lamp_count_consume_push_returns_qty():
    body = _inv_push(EVT_LAMP_CONSUME, [_inv_item(lamp.ITEM_LAMP, 9980, uid=42)])
    assert lamp.extract_lamp_count(body) == 9980


def test_extract_lamp_count_login_snapshot_returns_qty():
    # works for ANY evt_type, including a full inventory snapshot carrying 1001
    body = _inv_push(EVT_SNAPSHOT, [
        _inv_item(4001, 500),                 # pickaxe — ignored
        _inv_item(lamp.ITEM_LAMP, 510000),    # 神燈
    ])
    assert lamp.extract_lamp_count(body) == 510000


def test_extract_lamp_count_without_1001_returns_none():
    body = _inv_push(EVT_LAMP_CONSUME, [_inv_item(4001, 500), _inv_item(4003, 12)])
    assert lamp.extract_lamp_count(body) is None


# --- compute_lamp_target: four combos + clamp + 51万 example ----------------

def test_compute_lamp_target_feature_off_uses_total():
    # neither percent nor min_keep -> open the whole lot (rounded), clamped
    assert lamp.compute_lamp_target(
        100, lamp_percent=0.0, lamp_min_keep=0, max_open=10000) == 100


def test_compute_lamp_target_percent_only():
    # 1% of 1,000,000 = 10,000
    assert lamp.compute_lamp_target(
        1_000_000, lamp_percent=1.0, lamp_min_keep=0, max_open=10000) == 10000


def test_compute_lamp_target_min_keep_only():
    # keep 50万 of 51万 -> open 10,000
    assert lamp.compute_lamp_target(
        510000, lamp_percent=0.0, lamp_min_keep=500000, max_open=10000) == 10000


def test_compute_lamp_target_both_takes_smaller_511k_example():
    # 51万 total, 50% (=255000) AND keep 50万 (floor_cap=10000) -> min = 10000
    assert lamp.compute_lamp_target(
        510000, lamp_percent=50.0, lamp_min_keep=500000, max_open=20000) == 10000


def test_compute_lamp_target_clamped_to_max_open():
    # 50% of 1,000,000 = 500,000 but max_open caps it at 10000
    assert lamp.compute_lamp_target(
        1_000_000, lamp_percent=50.0, lamp_min_keep=0, max_open=10000) == 10000


def test_compute_lamp_target_rounds_to_multiple_of_20():
    # 1% of 1001 = 10.01 -> rounds to 20
    assert lamp.compute_lamp_target(
        1001, lamp_percent=1.0, lamp_min_keep=0, max_open=10000) == 20


def test_compute_lamp_target_min_keep_above_total_yields_zero():
    assert lamp.compute_lamp_target(
        100, lamp_percent=0.0, lamp_min_keep=500, max_open=10000) == 0


# --- lamp_daily_min tests ---------------------------------------------------

def test_compute_lamp_target_daily_min_boosts_percent_limited():
    # percent=1% of 100000=1000, but daily_min=2000 not yet met -> boost to 2000
    assert lamp.compute_lamp_target(
        100000, lamp_percent=1.0, lamp_min_keep=0, max_open=10000,
        lamp_daily_min=2000, opened_today=0) == 2000


def test_compute_lamp_target_daily_min_no_boost_when_already_met():
    # daily_min=100, opened_today=100 -> remaining_daily=0, no boost
    # percent=1% of 100000=1000 -> normal target
    assert lamp.compute_lamp_target(
        100000, lamp_percent=1.0, lamp_min_keep=0, max_open=10000,
        lamp_daily_min=100, opened_today=100) == 1000


def test_compute_lamp_target_daily_min_overrides_percent_with_min_keep():
    # percent=1% of 510000=5100, min_keep=500000 -> floor_cap=10000 -> normal=5100
    # daily_min=8000 not yet met -> boost to 8000 (still within floor_cap)
    assert lamp.compute_lamp_target(
        510000, lamp_percent=1.0, lamp_min_keep=500000, max_open=20000,
        lamp_daily_min=8000, opened_today=0) == 8000


def test_compute_lamp_target_daily_min_overrides_min_keep_floor():
    # total=1000, min_keep=900 -> floor_cap=100, daily_min=200.
    # daily floor is a hard guarantee: it overrides the reserve -> 200 (not 100).
    assert lamp.compute_lamp_target(
        1000, lamp_percent=0.0, lamp_min_keep=900, max_open=10000,
        lamp_daily_min=200, opened_today=0) == 200


def test_compute_lamp_target_daily_min_overrides_reserve_when_below_floor():
    # stock(300000) < min_keep(500000) -> floor_cap=0, % rule opens nothing,
    # but daily_min=8000 must still drive the target to 8000.
    assert lamp.compute_lamp_target(
        300000, lamp_percent=1.0, lamp_min_keep=500000, max_open=10000,
        lamp_daily_min=8000, opened_today=0) == 8000


def test_compute_lamp_target_daily_min_partial_day():
    # percent=1% of 100000=1000; daily_min=2000, opened_today=1500 -> remaining=500
    # 500 < normal 1000 -> no boost, stays at 1000
    assert lamp.compute_lamp_target(
        100000, lamp_percent=1.0, lamp_min_keep=0, max_open=10000,
        lamp_daily_min=2000, opened_today=1500) == 1000


def test_compute_lamp_target_daily_min_partial_day_boosts():
    # percent=1% of 100000=1000; daily_min=3000, opened_today=1500 -> remaining=1500
    # 1500 > normal 1000 -> boost to 1500 (rounded to nearest 20 = 1500)
    assert lamp.compute_lamp_target(
        100000, lamp_percent=1.0, lamp_min_keep=0, max_open=10000,
        lamp_daily_min=3000, opened_today=1500) == 1500


def test_compute_lamp_target_daily_min_no_override_when_normal_higher():
    # percent=50% of 10000=5000, daily_min=100, opened_today=0 -> normal is higher
    assert lamp.compute_lamp_target(
        10000, lamp_percent=50.0, lamp_min_keep=0, max_open=10000,
        lamp_daily_min=100, opened_today=0) == 5000


def test_compute_lamp_target_daily_target_is_fixed_and_tracks_progress():
    assert lamp.compute_lamp_target(
        500000, lamp_percent=1.0, lamp_min_keep=500000, max_open=10000,
        lamp_daily_target=200, opened_today=0) == 200
    assert lamp.compute_lamp_target(
        500000, lamp_percent=1.0, lamp_min_keep=500000, max_open=10000,
        lamp_daily_target=8000, opened_today=7960) == 40


# --- open_lamp fake-client harness ------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


def _base_extra(open_reply):
    # empty equip_info -> derive_set_map does no OCR; empty tab info.
    return {
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, b"")],
        CMD_OPEN_ALL: open_reply,
    }


def test_open_lamp_feature_off_unchanged_opens_until_out_of_lamps():
    # No percent / min_keep: behaves exactly as before — open each batch until
    # the server returns no uids, target reported = max_batches * batch_num.
    state = {"n": 0}

    def open_reply(_b):
        state["n"] += 1
        if state["n"] <= 2:                       # two real batches, then dry
            base = 1000 + state["n"]
            return [s2c(CMD_OPEN_ALL, _open_all_s2c([base]))]
        return [s2c(CMD_OPEN_ALL, b"")]           # out of lamps -> empty

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=1, max_batches=5,
                             push_wait=0)
        assert res["opened"] == 2                 # stopped on out-of-lamps
        assert res["target"] == 5                 # max_batches * batch_num
        assert res["initial_count"] is None       # count never required
        assert res["remaining"] is None
    finally:
        c.close()


def test_open_lamp_initial_count_zero_target_opens_nothing():
    # total below min_keep -> target 0 -> open nothing, return immediately.
    opened_calls = {"n": 0}

    def open_reply(_b):
        opened_calls["n"] += 1
        return [s2c(CMD_OPEN_ALL, _open_all_s2c([1]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_min_keep=500, initial_count=100, push_wait=0)
        assert res["opened"] == 0
        assert res["target"] == 0
        assert res["initial_count"] == 100
        assert opened_calls["n"] == 0             # never called 0x0509
    finally:
        c.close()


def test_open_lamp_reaches_target_then_stops():
    # initial_count=200, percent=10% -> target 20 -> exactly one batch of 20.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 600 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 200 - batches["n"] * 20
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_percent=10.0, initial_count=200, push_wait=0.5)
        assert res["target"] == 20
        assert res["opened"] == 20
        assert batches["n"] == 1                  # stopped after hitting target
        assert res["initial_count"] == 200
        assert res["remaining"] == 180
    finally:
        c.close()


def test_open_lamp_min_keep_stops_early_when_remaining_hits_floor():
    # initial_count=100, min_keep=60 -> floor_cap=40 -> target 40 (two batches).
    # But have the server report remaining=60 after the FIRST batch so the
    # min_keep guard stops before target is reached.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 700 + batches["n"] * 100
        uids = list(range(first, first + 20))
        # report remaining already AT the floor after the first batch
        remaining = 60 if batches["n"] == 1 else 40
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_min_keep=60, initial_count=100, push_wait=0.5)
        assert res["target"] == 40                # floor_cap = 100 - 60
        assert res["opened"] == 20                # stopped early on the guard
        assert batches["n"] == 1
        assert res["remaining"] == 60
    finally:
        c.close()


def test_open_lamp_daily_min_digs_below_min_keep_floor():
    # stock(100) < min_keep(500): floor_cap=0 so the % rule opens nothing, and
    # remaining is always <= min_keep. daily_min=80 must override the reserve
    # and open the full daily quota (4 batches) instead of stopping at batch 1.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 1500 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 100 - batches["n"] * 20       # 80,60,40,20 — all <= min_keep
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_min_keep=500, lamp_daily_min=80,
                             opened_today=0, initial_count=100, push_wait=0.5)
        assert res["target"] == 80                # daily floor, not reserve-clamped
        assert res["opened"] == 80                # dug below the 500 reserve
        assert batches["n"] == 4
    finally:
        c.close()


def test_open_lamp_daily_target_ignores_min_keep_until_quota():
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 1800 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 500000 - batches["n"] * 20
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_min_keep=500000, lamp_daily_target=40,
                             initial_count=500000, push_wait=0.5)
        assert res["opened"] == 40
        assert batches["n"] == 2
    finally:
        c.close()


def test_open_lamp_out_of_lamps_stops_before_target():
    # target wants 60 but server runs dry on the 2nd batch.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        if batches["n"] == 1:
            uids = list(range(800, 820))
            return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                    s2c(CMD_INVENTORY_PUSH,
                        _inv_push(EVT_LAMP_CONSUME,
                                  [_inv_item(lamp.ITEM_LAMP, 80)]))]
        return [s2c(CMD_OPEN_ALL, b"")]           # out of lamps

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_percent=60.0, initial_count=100, push_wait=0.5)
        assert res["target"] == 60
        assert res["opened"] == 20                # stopped on out-of-lamps
        assert batches["n"] == 2
    finally:
        c.close()


def test_open_lamp_initial_count_none_derives_total_from_first_batch():
    # No initial_count: open first batch (20), read remaining=180 from its
    # 1001006 push -> total = 180 + 20 = 200; percent 10% -> target 20 -> stop.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 900 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 200 - batches["n"] * 20
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_percent=10.0, initial_count=None,
                             push_wait=0.5)
        assert res["initial_count"] == 200        # derived 180 + 20
        assert res["target"] == 20
        assert res["opened"] == 20
        assert batches["n"] == 1
    finally:
        c.close()


def test_open_lamp_on_progress_called_once_per_batch():
    # initial_count=120, percent=50% -> target 60 -> three batches of 20.
    progress = []

    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 1100 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 120 - batches["n"] * 20
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_percent=50.0, initial_count=120,
                             push_wait=0.5,
                             on_progress=lambda o, t: progress.append((o, t)))
        assert res["target"] == 60
        assert res["opened"] == 60
        assert progress == [(20, 60), (40, 60), (60, 60)]
    finally:
        c.close()


def test_open_lamp_progress_callback_exception_does_not_abort():
    # A raising on_progress must not break the open loop.
    batches = {"n": 0}

    def open_reply(_b):
        batches["n"] += 1
        first = 1300 + batches["n"] * 100
        uids = list(range(first, first + 20))
        remaining = 120 - batches["n"] * 20
        return [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                s2c(CMD_INVENTORY_PUSH,
                    _inv_push(EVT_LAMP_CONSUME,
                              [_inv_item(lamp.ITEM_LAMP, remaining)]))]

    def boom(_o, _t):
        raise RuntimeError("dashboard down")

    c, _fake = _client(_base_extra(open_reply))
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=20, max_batches=10,
                             lamp_percent=50.0, initial_count=120,
                             push_wait=0.5, on_progress=boom)
        assert res["opened"] == 60                # loop still completed
        assert res["target"] == 60
    finally:
        c.close()


# --- should_abort (interruptible WS phase) ----------------------------------

def test_open_lamp_should_abort_raises_before_first_batch():
    """should_abort true at the loop top -> WSRunAborted before any open RPC,
    so the in-progress lamp task is left pending for the resume."""
    from ws_token.abort import WSRunAborted

    sent = {"n": 0}

    def open_reply(_b):
        sent["n"] += 1                            # must NOT be reached
        return [s2c(CMD_OPEN_ALL, _open_all_s2c([1001]))]

    c, _fake = _client(_base_extra(open_reply))
    try:
        with pytest.raises(WSRunAborted):
            lamp.open_lamp(c, dry_run=True, batch_num=1, max_batches=5,
                           push_wait=0, should_abort=lambda: True)
        assert sent["n"] == 0                      # no batch was opened
    finally:
        c.close()

"""Tests for ws_token.lamp v2 — combo->套裝 auto-equip over pure WS.

Decision/comparison reuse opengold_v2 (SkillEvaluator). This verifies the WS glue:
request wires, p_equip parsing, the affix->code adapter, set-map derivation from
equip_info (combo->tab + per-tab 連閃), decide_v2 branches, and the open loop's
equip + chunked-sell + restore-active-tab behavior (dry-run vs live).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from opengold_v2.config import OpenGoldConfig  # noqa: E402
from opengold_v2.ocr_parser import OCRParser  # noqa: E402
from ws_token import codec, lamp  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from tests.fakes.ws_fakes import CREDS, FakeTransport, factory_for, login_responder, s2c  # noqa: E402

CFG = OpenGoldConfig()
PARSER = OCRParser(CFG)

CMD_EQUIP_INFO = 0x0501
CMD_WEAR = 0x0502
CMD_EQUIP_CHANGE = 0x0504
CMD_SELL = 0x0505
CMD_OPEN_ALL = 0x0509
CMD_TAB_INFO = 0x0510
CMD_CHOOSE_TAB = 0x0511

# affix ids
A_K, A_Y, A_B, A_L, A_S, A_F, A_H = 1037, 1023, 1004, 1016, 1008, 1017, 1012
# combos as Chinese-code frozensets (what drop_to_equipment yields)
C_KY = frozenset({"技", "暈"})
C_BL = frozenset({"爆", "連"})
C_BS = frozenset({"爆", "閃"})
C_LS = frozenset({"連", "閃"})
C_FH = frozenset({"反", "回"})


def _tmpl(rarity, slot, item=1):
    return rarity * 10**7 + slot * 10**5 + item


def _p_equip(uid, tmpl, affixes, location=0, tab=1):
    b = codec.pb_uint(1, uid) + codec.pb_uint(2, tmpl)
    if location:
        b += codec.pb_uint(4, location)
    b += codec.pb_uint(5, tab)
    for aid, val in affixes.items():
        b += codec.pb_msg(7, codec.pb_uint(1, aid) + codec.pb_uint(2, val))
    return b


def _equip_info_body(items):
    return b"".join(codec.pb_msg(1, it) for it in items)


def _change_body(items):
    return codec.pb_uint(1, 1) + codec.pb_uint(2, 0) + b"".join(codec.pb_msg(3, it) for it in items)


def _open_all_s2c(uids):
    return b"".join(codec.pb_uint(1, u) for u in uids)


def _drop(rarity, slot, affixes, uid=1):
    return {"uid": uid, "rarity": rarity, "slot": slot, "affixes": affixes}


# --- request wires ---------------------------------------------------------

def test_build_open_all_and_sell_wires():
    assert lamp.build_open_all(20, 0) == codec.pb_uint(1, 20) + codec.pb_uint(2, 0)
    assert lamp.build_sell([5, 6]) == codec.pb_uint(1, 5) + codec.pb_uint(1, 6)


def test_build_wear_and_choose_tab_wires():
    assert lamp.build_wear(5, 99) == codec.pb_uint(1, 5) + codec.pb_uint(2, 99)
    assert lamp.build_choose_tab(3) == codec.pb_uint(1, 3)


def test_parse_tab_info_active_tab():
    assert lamp.parse_tab_info(codec.pb_uint(1, 7)) == 7


# --- affix -> Equipment ----------------------------------------------------

def test_drop_to_equipment_maps_and_skips_companion():
    eq = lamp.drop_to_equipment({"affixes": {A_K: 387, A_F: 250}}, PARSER)
    assert [(e.code, e.probability) for e in eq.entries] == [("技", 3.87), ("反", 2.5)]
    eq2 = lamp.drop_to_equipment({"affixes": {4001: 999, A_H: 300}}, PARSER)
    assert [(e.code, e.probability) for e in eq2.entries] == [("回", 3.0)]


# --- derive_set_map --------------------------------------------------------

def test_derive_set_map_clean_lian_and_outliers():
    items = []
    for sl in range(1, 11):
        items.append(_p_equip(2000 + sl, _tmpl(11, sl), {A_K: 300, A_Y: 300}, location=sl, tab=2))  # KY
        items.append(_p_equip(1000 + sl, _tmpl(11, sl), {A_B: 300, A_L: 300}, location=sl, tab=1))  # BL
    # tab5: lian build 6 BS + 4 LS
    for sl in range(1, 7):
        items.append(_p_equip(5000 + sl, _tmpl(11, sl), {A_B: 300, A_S: 300}, location=sl, tab=5))
    for sl in range(7, 11):
        items.append(_p_equip(5000 + sl, _tmpl(11, sl), {A_L: 300, A_S: 300}, location=sl, tab=5))
    # tab3: 8 FH + 1 stray KY + 1 stray BF -> dominant FH
    for sl in range(1, 9):
        items.append(_p_equip(3000 + sl, _tmpl(11, sl), {A_F: 300, A_H: 300}, location=sl, tab=3))
    items.append(_p_equip(3009, _tmpl(11, 9), {A_K: 300, A_Y: 300}, location=9, tab=3))
    items.append(_p_equip(3010, _tmpl(11, 10), {A_B: 300, A_F: 300}, location=10, tab=3))

    set_map, lian, worn = lamp.derive_set_map(_equip_info_body(items), PARSER)
    assert set_map[C_KY] == 2
    assert set_map[C_BL] == 1
    assert set_map[C_BS] == 5 and set_map[C_LS] == 5
    assert set_map[C_FH] == 3
    assert lian == {5}
    assert worn[2][1]["uid"] == 2001 and worn[5][7]["uid"] == 5007


def test_derive_set_map_complete_tab_wins_combo_collision():
    # Two tabs share dominant 暈回; the COMPLETE one (more items) must win,
    # regardless of iteration order. Put the incomplete tab7 first.
    items = []
    for sl in range(1, 5):  # tab7: only 4 HY (+6 single-回, ignored)
        items.append(_p_equip(7000 + sl, _tmpl(11, sl), {A_Y: 300, A_H: 300}, location=sl, tab=7))
    for sl in range(5, 11):
        items.append(_p_equip(7000 + sl, _tmpl(11, sl), {A_H: 300}, location=sl, tab=7))
    for sl in range(1, 11):  # tab6: full 10 HY
        items.append(_p_equip(6000 + sl, _tmpl(11, sl), {A_Y: 300, A_H: 300}, location=sl, tab=6))
    set_map, _lian, _worn = lamp.derive_set_map(_equip_info_body(items), PARSER)
    assert set_map[frozenset({"暈", "回"})] == 6  # complete tab6 (10) beats tab7 (4)


# --- decide_v2 -------------------------------------------------------------

SET_MAP = {C_KY: 2, C_BL: 1, C_BS: 5, C_LS: 5, C_FH: 3, frozenset({"連", "回"}): 10}
LIAN = {5}


def _worn(tab_slot_affixes):
    worn = {}
    for (tab, slot), (uid, aff) in tab_slot_affixes.items():
        worn.setdefault(tab, {})[slot] = {"uid": uid, "rarity": 11, "slot": slot, "affixes": aff}
    return worn


def test_decide_v2_sells_below_rarity():
    d = lamp.decide_v2(_drop(5, 1, {A_K: 300, A_Y: 300}), SET_MAP, {}, LIAN, CFG, PARSER)
    assert d.action == "sell" and "rarity" in d.reason


def test_decide_v2_set_combo_in_unwanted_is_still_compared_not_sold():
    # 連回 is in opengold unwanted_combos, but tab10 is a 連回 set -> must compare, not auto-sell
    worn = _worn({(10, 1): (700, {A_L: 100, A_H: 100})})
    d = lamp.decide_v2(_drop(11, 1, {A_L: 500, A_H: 500}), SET_MAP, worn, LIAN, CFG, PARSER)
    assert d.action == "equip" and d.tab == 10 and d.displaced_uid == 700


def test_decide_v2_unmapped_unwanted_sells():
    # 爆暈 maps to no set and is an unwanted combo -> sell
    d = lamp.decide_v2(_drop(11, 1, {A_B: 300, A_Y: 300}), SET_MAP, {}, LIAN, CFG, PARSER)
    assert d.action == "sell"


def test_decide_v2_unmapped_not_unwanted_leaves():
    # 技回: not in this account's sets (SET_MAP) and not in the unwanted list -> leave
    d = lamp.decide_v2(_drop(11, 1, {A_K: 300, A_H: 300}), SET_MAP, {}, LIAN, CFG, PARSER)
    assert d.action == "leave"


def test_decide_v2_win_equips_and_marks_displaced():
    worn = _worn({(2, 1): (210, {A_K: 100, A_Y: 100})})
    d = lamp.decide_v2(_drop(11, 1, {A_K: 500, A_Y: 100}), SET_MAP, worn, LIAN, CFG, PARSER)
    assert d.action == "equip" and d.tab == 2 and d.displaced_uid == 210


def test_decide_v2_lose_sells():
    worn = _worn({(2, 1): (210, {A_K: 500, A_Y: 500})})
    d = lamp.decide_v2(_drop(11, 1, {A_K: 100, A_Y: 100}), SET_MAP, worn, LIAN, CFG, PARSER)
    assert d.action == "sell"


def test_decide_v2_empty_slot_in_set_equips_without_displaced():
    d = lamp.decide_v2(_drop(11, 4, {A_K: 300, A_Y: 300}), SET_MAP, {2: {}}, LIAN, CFG, PARSER)
    assert d.action == "equip" and d.tab == 2 and d.displaced_uid is None


# --- open_lamp v2 loop -----------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def _standard_worn_info():
    # tab2 = KY set; slot1 worn weak so a strong KY drop wins
    items = [_p_equip(2000 + sl, _tmpl(11, sl), {A_K: 100, A_Y: 100}, location=sl, tab=2)
             for sl in range(1, 11)]
    return _equip_info_body(items)


def test_open_lamp_v2_equips_winner_sells_rest_and_restores_tab():
    info = _standard_worn_info()
    # drops: 301 strong KY slot1 (win -> equip tab2, displaces 2001), 302 rarity5 (sell)
    drops = _change_body([_p_equip(301, _tmpl(11, 1), {A_K: 600, A_Y: 100}),
                          _p_equip(302, _tmpl(5, 2), {A_K: 100, A_Y: 100})])
    c, fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],   # active tab 1
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: lambda _b: [s2c(CMD_OPEN_ALL, _open_all_s2c([301, 302])),
                                  s2c(CMD_EQUIP_CHANGE, drops)],
        CMD_WEAR: lambda _b: [s2c(CMD_WEAR, b"")],
        CMD_SELL: lambda _b: [s2c(CMD_SELL, b"")],
        CMD_CHOOSE_TAB: lambda _b: [s2c(CMD_CHOOSE_TAB, b"")],
    })
    try:
        res = lamp.open_lamp(c, dry_run=False, batch_num=2, max_batches=1)
        assert [(t, u) for t, u, _ in res["equipped"]] == [(2, 301)]
        assert set(u for u, _ in res["sold"]) == {2001, 302}  # displaced worn + rarity reject
        # wear sent with tab=2, uid=301
        wear = next(b for _s, c2, b in fake.framed_sent() if c2 == CMD_WEAR)
        assert codec.walk_dict(wear) == {1: 2, 2: 301}
        # restored active tab 1
        chosen = next(b for _s, c2, b in fake.framed_sent() if c2 == CMD_CHOOSE_TAB)
        assert codec.walk_dict(chosen) == {1: 1}
    finally:
        c.close()


def test_open_lamp_v2_dry_run_sends_no_actions():
    info = _standard_worn_info()
    drops = _change_body([_p_equip(301, _tmpl(11, 1), {A_K: 600, A_Y: 100}),
                          _p_equip(302, _tmpl(5, 2), {A_K: 100, A_Y: 100})])
    c, fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: lambda _b: [s2c(CMD_OPEN_ALL, _open_all_s2c([301, 302])),
                                  s2c(CMD_EQUIP_CHANGE, drops)],
    })
    try:
        res = lamp.open_lamp(c, dry_run=True, batch_num=2, max_batches=1)
        assert [(t, u) for t, u, _ in res["equipped"]] == [(2, 301)]
        assert {u for u, _ in res["sold"]} == {2001, 302}
        sent = fake.sent_cmds()
        assert CMD_WEAR not in sent and CMD_SELL not in sent and CMD_CHOOSE_TAB not in sent
    finally:
        c.close()


def test_open_lamp_v2_sells_twenty_rejects_in_one_request():
    info = _standard_worn_info()
    uids = list(range(401, 421))
    drops = _change_body([
        _p_equip(uid, _tmpl(5, 1), {A_K: 100, A_Y: 100})
        for uid in uids
    ])
    c, fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: lambda _b: [s2c(CMD_OPEN_ALL, _open_all_s2c(uids)),
                                  s2c(CMD_EQUIP_CHANGE, drops)],
        CMD_SELL: lambda _b: [s2c(CMD_SELL, b"")],
        CMD_CHOOSE_TAB: lambda _b: [s2c(CMD_CHOOSE_TAB, b"")],
    })
    try:
        res = lamp.open_lamp(c, dry_run=False, batch_num=20, max_batches=1)
        assert {uid for uid, _reason in res["sold"]} == set(uids)
        sell_bodies = [b for _s, cmd, b in fake.framed_sent() if cmd == CMD_SELL]
        assert len(sell_bodies) == 1
        assert sell_bodies[0] == lamp.build_sell(uids)
    finally:
        c.close()


def test_open_lamp_v2_waits_after_sell_before_next_command(monkeypatch):
    info = _standard_worn_info()
    uid = 430
    drops = _change_body([_p_equip(uid, _tmpl(5, 1), {A_K: 100, A_Y: 100})])
    events = []

    def open_reply(_body):
        events.append("open")
        return [
            s2c(CMD_EQUIP_CHANGE, drops),
            s2c(CMD_OPEN_ALL, _open_all_s2c([uid])),
        ]

    def sell_reply(_body):
        events.append("sell")
        return [s2c(CMD_SELL, b"")]

    def choose_reply(_body):
        events.append("choose")
        return [s2c(CMD_CHOOSE_TAB, b"")]

    monkeypatch.setattr(lamp.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    c, _fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: open_reply,
        CMD_SELL: sell_reply,
        CMD_CHOOSE_TAB: choose_reply,
    })
    try:
        res = lamp.open_lamp(c, dry_run=False, batch_num=1, max_batches=1)
        assert {sold_uid for sold_uid, _reason in res["sold"]} == {uid}
        assert events == ["open", "sell", ("sleep", 0.3), "choose"]
    finally:
        c.close()


def test_open_lamp_v2_survives_open_exception():
    # a connection drop mid-run raises a non-timeout error on the open send;
    # the loop must stop gracefully (return partial), not crash.
    info = _standard_worn_info()
    c, _fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
    })
    orig = c.call_for

    def boom(cmd, *a, **k):
        if cmd == CMD_OPEN_ALL:
            raise RuntimeError("connection closed")
        return orig(cmd, *a, **k)

    c.call_for = boom
    try:
        res = lamp.open_lamp(c, dry_run=False, batch_num=1, max_batches=3)
        assert res["opened"] == 0  # stopped gracefully
    finally:
        c.close()


def test_open_lamp_v2_waits_between_batches(monkeypatch):
    info = _standard_worn_info()
    opened_batches = []

    def open_reply(_body):
        idx = len(opened_batches)
        opened_batches.append(idx)
        return [s2c(CMD_OPEN_ALL, _open_all_s2c([400 + idx]))]

    c, _fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: open_reply,
    })
    sleep_calls = []
    monkeypatch.setattr(lamp.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    try:
        res = lamp.open_lamp(
            c,
            dry_run=True,
            batch_num=1,
            max_batches=2,
            batch_delay=0.2,
            push_wait=0,
        )
        assert res["opened"] == 2
        assert sleep_calls == [0.2]
    finally:
        c.close()


def test_open_lamp_v2_tolerates_sell_timeout():
    info = _standard_worn_info()
    drops = _change_body([_p_equip(302, _tmpl(5, 2), {A_K: 100, A_Y: 100})])  # one reject
    c, fake = _client({
        CMD_TAB_INFO: lambda _b: [s2c(CMD_TAB_INFO, codec.pb_uint(1, 1))],
        CMD_EQUIP_INFO: lambda _b: [s2c(CMD_EQUIP_INFO, info)],
        CMD_OPEN_ALL: lambda _b: [s2c(CMD_OPEN_ALL, _open_all_s2c([302])),
                                  s2c(CMD_EQUIP_CHANGE, drops)],
        CMD_SELL: lambda _b: [],          # withhold the 0x0505 reply -> must not crash
        CMD_CHOOSE_TAB: lambda _b: [s2c(CMD_CHOOSE_TAB, b"")],
    })
    try:
        res = lamp.open_lamp(c, dry_run=False, batch_num=1, max_batches=1, sell_timeout=0.2)
        assert {u for u, _ in res["sold"]} == {302}
    finally:
        c.close()

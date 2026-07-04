"""Tests for ws_token.relic — 遺物 read + 強化 (level-up / point allocation).

Schemas are the LIVE-captured truth (docs/protocol/RELIC_ALLOC_RECON.md,
cross-checked vs docs/protocol/TYPE_PROTO_SCHEMA.json):

  relic module 17 (0x11); cmd = module*256 + sub:
    relic_info     0x1101  c2s {}                  -> s2c { relic_list#1: p_relic[] }
    relic_up       0x1103  c2s { relic_uid#1:u64 } -> s2c { p_relic#1 }
    relic_tab_info 0x1105  c2s {}                  -> s2c { tab#1, tab_list#2: p_relic_tab_info[] }
  p_relic { id#1:u64, cfg_id#2, type#3, location#4, lv#5 }

LIVE capture (小寶 7fe98fc6, 2026-06-14):
  tx 0x1103 {1: 89562953023846}
  rx 0x1103 {1: p_relic{id=89562953023846, cfg_id=4017, type=1, location=1, lv=100}}
  (Lv.99 -> 100, fragments -442080: server-authoritative.)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token import relic  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.relic import (  # noqa: E402
    CMD_ERROR,
    CMD_RELIC_INFO,
    CMD_RELIC_TAB_INFO,
    CMD_RELIC_UP,
    RELIC_FRAGMENT_ITEM,
    Relic,
    RelicInfo,
    RelicTabInfo,
    build_relic_up_body,
    parse_relic_info,
    parse_relic_tab_info,
    parse_upgrade,
    plan_balanced_upgrades,
    read_plans,
    read_relics,
    upgrade_relic,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- helpers: build p_relic / p_relic_tab_info wire bodies ------------------


def _p_relic(uid, cfg_id, quality, location, lv):
    return (codec.pb_uint(1, uid) + codec.pb_uint(2, cfg_id)
            + codec.pb_uint(3, quality) + codec.pb_uint(4, location)
            + codec.pb_uint(5, lv))


def _relic_info_body(relics):
    return b"".join(codec.pb_msg(1, _p_relic(*r)) for r in relics)


def _kv(k, v):
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _tab_info_body(active_tab, plans):
    out = codec.pb_uint(1, active_tab)
    for tab, name, positions in plans:
        sub = codec.pb_uint(1, tab) + codec.pb_str(2, name)
        for pos, cfg in positions:
            sub += codec.pb_msg(3, _kv(pos, cfg))
        out += codec.pb_msg(2, sub)
    return out


# --- build relic_up body: {relic_uid#1} -------------------------------------


def test_build_relic_up_body_is_uid_only():
    # Arrange / Act
    body = build_relic_up_body(89562953023846)
    # Assert — the LIVE c2s carried ONLY field 1 (the relic uid)
    assert codec.walk_dict(body) == {1: 89562953023846}


def test_build_relic_up_body_matches_live_capture():
    # Arrange / Act
    body = build_relic_up_body(89562953023846)
    # Assert — exact LIVE-captured bytes (08 e6828080d0ae14)
    assert body == bytes.fromhex("08e6828080d0ae14")


# --- parse_relic_info -------------------------------------------------------


def test_parse_relic_info_decodes_relics():
    # Arrange
    body = _relic_info_body([
        (89562953023846, 4017, 1, 1, 99),
        (89562953023528, 4025, 1, 0, 96),
    ])
    # Act
    info = parse_relic_info(CMD_RELIC_INFO, body)
    # Assert
    assert isinstance(info, RelicInfo) and info.success
    assert info.relics[0] == Relic(89562953023846, 4017, 1, 1, 99)
    assert info.relics[1].location == 0 and info.relics[1].level == 96


def test_parse_relic_info_equipped_filters_location_zero():
    # Arrange
    body = _relic_info_body([
        (1, 4001, 2, 1, 50),
        (2, 4002, 2, 0, 60),  # unequipped
        (3, 4003, 3, 2, 70),
    ])
    # Act
    info = parse_relic_info(CMD_RELIC_INFO, body)
    # Assert
    assert tuple(r.uid for r in info.equipped) == (1, 3)


def test_parse_relic_info_error_frame():
    # Arrange
    body = codec.pb_uint(1, 25)
    # Act
    info = parse_relic_info(CMD_ERROR, body)
    # Assert
    assert info.success is False and info.error_code == 25


# --- parse_relic_tab_info ---------------------------------------------------


def test_parse_relic_tab_info_decodes_plans():
    # Arrange — LIVE shape: active tab 3, plan '法師' positions 1->4025, 2->4001
    body = _tab_info_body(3, [
        (1, "打回復", [(1, 4025), (2, 4001)]),
        (2, "法師", [(1, 4025), (3, 4008)]),
    ])
    # Act
    info = parse_relic_tab_info(CMD_RELIC_TAB_INFO, body)
    # Assert
    assert isinstance(info, RelicTabInfo)
    assert info.active_tab == 3
    assert info.plans[1].name == "法師"
    assert info.plans[1].positions == {1: 4025, 3: 4008}


# --- parse_upgrade ----------------------------------------------------------


def test_parse_upgrade_success_returns_updated_relic():
    # Arrange — exact LIVE s2c body
    body = bytes.fromhex("0a1108e6828080d0ae1410b11f180120012864")
    # Act
    res = parse_upgrade(CMD_RELIC_UP, body)
    # Assert
    assert res.success
    assert res.relic == Relic(89562953023846, 4017, 1, 1, 100)


def test_parse_upgrade_error_frame_is_failure():
    # Arrange
    body = codec.pb_uint(1, 7)
    # Act
    res = parse_upgrade(CMD_ERROR, body)
    # Assert
    assert res.success is False and res.error_code == 7


# --- live calls against the fake transport ----------------------------------


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_read_relics_sends_empty_body_and_parses():
    # Arrange
    body = _relic_info_body([(10, 4001, 2, 1, 80)])
    c, fake = _client({CMD_RELIC_INFO: lambda _b: [s2c(CMD_RELIC_INFO, body)]})
    try:
        # Act
        info = read_relics(c)
        # Assert
        assert info.success and info.relics[0].uid == 10
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_RELIC_INFO]
        assert sent[0] == b""  # empty c2s body
    finally:
        c.close()


def test_read_plans_parses_tab_info():
    # Arrange
    body = _tab_info_body(1, [(1, "推圖", [(1, 4017)])])
    c, fake = _client(
        {CMD_RELIC_TAB_INFO: lambda _b: [s2c(CMD_RELIC_TAB_INFO, body)]})
    try:
        # Act
        info = read_plans(c)
        # Assert
        assert info.active_tab == 1 and info.plans[0].name == "推圖"
    finally:
        c.close()


def test_upgrade_relic_sends_uid_and_parses_updated_relic():
    # Arrange
    reply = bytes.fromhex("0a1108e6828080d0ae1410b11f180120012864")
    c, fake = _client({CMD_RELIC_UP: lambda _b: [s2c(CMD_RELIC_UP, reply)]})
    try:
        # Act
        res = upgrade_relic(c, 89562953023846)
        # Assert
        assert res.success and res.relic.level == 100
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_RELIC_UP]
        assert codec.walk_dict(sent[0]) == {1: 89562953023846}
    finally:
        c.close()


def test_upgrade_relic_error_is_failure():
    # Arrange — server replies 0x0201 (e.g. insufficient fragments)
    c, fake = _client({CMD_RELIC_UP: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 25))]})
    try:
        # Act
        res = upgrade_relic(c, 123)
        # Assert
        assert res.success is False and res.error_code == 25
    finally:
        c.close()


# --- plan_balanced_upgrades (pure "平均" strategy) --------------------------


def _flat_cost(_level):
    return 100  # constant per-level cost for deterministic tests


def test_balanced_upgrade_picks_lowest_level_first():
    # Arrange — two relics, one behind; budget for exactly 3 steps
    steps = plan_balanced_upgrades(
        [(1, 10), (2, 12)], fragments=300, cost_at=_flat_cost)
    # Assert — bring the lower (uid 1, lv10) up first, then alternate to balance
    #   start (10,12) -> up1(11,12) -> up1(12,12) -> up either(tie->order) up1
    assert steps == [1, 1, 1]


def test_balanced_upgrade_alternates_when_level():
    # Arrange — equal levels, budget for 4 steps; tie broken by input order
    steps = plan_balanced_upgrades(
        [(1, 5), (2, 5)], fragments=400, cost_at=_flat_cost)
    # Assert — even distribution: 1,2,1,2
    assert steps == [1, 2, 1, 2]


def test_balanced_upgrade_stops_at_level_cap():
    # Arrange — relic at cap cannot be upgraded
    steps = plan_balanced_upgrades(
        [(1, 150)], fragments=10_000, cost_at=_flat_cost, level_cap=150)
    # Assert
    assert steps == []


def test_balanced_upgrade_stops_when_budget_exhausted():
    # Arrange — budget for 2 steps only
    steps = plan_balanced_upgrades(
        [(1, 1), (2, 1)], fragments=250, cost_at=_flat_cost)
    # Assert — 250 / 100 = 2 affordable steps
    assert len(steps) == 2


def test_balanced_upgrade_respects_max_steps():
    # Arrange
    steps = plan_balanced_upgrades(
        [(1, 1), (2, 1)], fragments=10_000, cost_at=_flat_cost, max_steps=3)
    # Assert
    assert len(steps) == 3


def test_balanced_upgrade_uses_real_rising_cost():
    # Arrange — cost rises with level; lower relic stays cheaper so gets priority
    def cost(level):
        return level * 10  # configRelic-like rising cost
    # relics at lv 2 and lv 5; budget 100
    steps = plan_balanced_upgrades(
        [(1, 2), (2, 5)], fragments=100, cost_at=cost)
    # Assert — uid1 (lower level, cheaper) upgraded repeatedly first
    #   lv2->3 cost20, 3->4 cost30, 4->5 cost40 = 90 used (3 steps), then lv5 vs5
    #   next cheapest step costs 50 (>10 left) -> stop
    assert steps == [1, 1, 1]
    assert steps.count(2) == 0


# --- spend_to_target should_stop (authoritative stop, frag_unknown safety) --


class _Tracker:
    def __init__(self, counts=None):
        self.counts = dict(counts or {})


def _spend_client(relics):
    levels = {uid: lv for uid, _c, _q, _l, lv in relics}
    cfg_of = {uid: (c, q, l) for uid, c, q, l, _lv in relics}
    info_body = _relic_info_body(relics)

    def _info(_b):
        return [s2c(CMD_RELIC_INFO, info_body)]

    def _up(body):
        uid = codec.walk_dict(body).get(1)
        levels[uid] += 1
        c, q, l = cfg_of[uid]
        return [s2c(CMD_RELIC_UP,
                    codec.pb_msg(1, _p_relic(uid, c, q, l, levels[uid])))]

    fake = FakeTransport(login_responder({CMD_RELIC_INFO: _info, CMD_RELIC_UP: _up}))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake


def _count_ups(fake):
    return sum(1 for _s, cmd, _b in fake.framed_sent() if cmd == CMD_RELIC_UP)


def test_spend_to_target_should_stop_halts_even_when_frag_unknown():
    """should_stop（server 權威停止）即使 frag_unknown 也能在達標時立即停止，
    不會跑到 max_steps（CRITICAL 保護：tracker 量不到時的唯一可靠界）。"""
    tracker = _Tracker({})  # no FRAG -> frag_unknown, tracker delta gate dead
    c, fake = _spend_client([(1, 4001, 1, 1, 10)])
    try:
        # should_stop fires after 3 upgrades, counted from the wire (tracker blind)
        out = relic.spend_to_target(
            c, tracker, target_spend=999_999, max_steps=50,
            should_stop=lambda: _count_ups(fake) >= 3)
        assert out["frag_unknown"] is True
        assert out["stopped"] == "target_reached"
        ups = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_RELIC_UP]
        assert len(ups) == 3  # stopped at should_stop, NOT max_steps(50)
    finally:
        c.close()


def test_spend_to_target_should_stop_error_stops_conservatively():
    """should_stop 拋例外時保守停止（不超量），記 stopped=should_stop_error。"""
    tracker = _Tracker({RELIC_FRAGMENT_ITEM: 1_000_000})
    c, fake = _spend_client([(1, 4001, 1, 1, 10)])

    def _boom():
        raise RuntimeError("accrued read blew up")

    try:
        out = relic.spend_to_target(
            c, tracker, target_spend=999_999, max_steps=50, should_stop=_boom)
        assert out["stopped"] == "should_stop_error"
        ups = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_RELIC_UP]
        assert len(ups) == 1  # one upgrade then the raising check stops the loop
    finally:
        c.close()

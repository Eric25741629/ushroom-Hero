"""Tests for ws_token.idle_reward — idle / offline reward over pure WS.

Schemas are the live-exported truth
(docs/protocol/MAIN_CHAPTER_PROTO_SCHEMA.json + TYPE_PROTO_SCHEMA.json):
  main_chapter_reward_info_c2s  { type#1:uint32 }
  main_chapter_reward_info_s2c  { type#1, time#2:uint32,
                                  res_list#3:p_reward[], item_list#4:p_reward[] }
  main_chapter_claim_reward_c2s { type#1:uint32 }
  main_chapter_claim_reward_s2c { type#1 }   (handler ack; body may be empty)
  p_reward { gtid#1:int32, num#2:int64 }
  type:  1 = online (在線, pulled via reward_info_c2s)
         2 = offline (離線, pushed by server after login)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.idle_reward import (  # noqa: E402
    AD_QUICK_2H_CONFIG_ID,
    CMD_AD_REWARD,
    CMD_CLAIM_REWARD,
    CMD_ERROR,
    CMD_REWARD_INFO,
    TYPE_OFFLINE,
    TYPE_ONLINE,
    ClaimResult,
    IdleReward,
    QuickClaimResult,
    build_ad_reward_body,
    build_claim_body,
    build_reward_info_body,
    claim,
    claim_offline,
    claim_offline_from_push,
    claim_online,
    claim_quick_2h,
    parse_reward_info,
    read_online,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


def _reward(gtid, num):
    # p_reward {gtid#1:int32, num#2:int64}
    return codec.pb_uint(1, gtid) + codec.pb_uint(2, num)


def _reward_info_body(type_, time_, res_list=(), item_list=()):
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, time_)
            + b"".join(codec.pb_msg(3, r) for r in res_list)
            + b"".join(codec.pb_msg(4, r) for r in item_list))


# --- build bodies: {type#1} -------------------------------------------------

def test_build_reward_info_body_is_type_only():
    # Arrange / Act
    body = build_reward_info_body(TYPE_ONLINE)
    # Assert
    assert codec.walk_dict(body) == {1: 1}


def test_build_claim_body_is_type_only():
    # Arrange / Act
    body = build_claim_body(TYPE_OFFLINE)
    # Assert
    assert codec.walk_dict(body) == {1: 2}


# --- parse_reward_info: type / time / res_list / item_list ------------------

def test_parse_reward_info_decodes_all_fields():
    # Arrange
    body = _reward_info_body(
        TYPE_OFFLINE, 3600,
        res_list=[_reward(1, 5000), _reward(2, 120)],
        item_list=[_reward(30001, 3)],
    )
    # Act
    r = parse_reward_info(body)
    # Assert
    assert isinstance(r, IdleReward)
    assert r.type == 2
    assert r.time == 3600
    assert r.res_list == [(1, 5000), (2, 120)]
    assert r.item_list == [(30001, 3)]


def test_parse_reward_info_empty_lists_not_claimable():
    # Arrange
    body = _reward_info_body(TYPE_ONLINE, 0)
    # Act
    r = parse_reward_info(body)
    # Assert
    assert r.res_list == [] and r.item_list == []
    assert r.is_claimable is False


def test_parse_reward_info_res_only_is_claimable():
    # Arrange
    body = _reward_info_body(TYPE_ONLINE, 60, res_list=[_reward(1, 10)])
    # Act / Assert
    assert parse_reward_info(body).is_claimable is True


def test_parse_reward_info_item_only_is_claimable():
    # Arrange
    body = _reward_info_body(TYPE_OFFLINE, 60, item_list=[_reward(7, 1)])
    # Act / Assert
    assert parse_reward_info(body).is_claimable is True


# --- read_online / claim against the fake transport ------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


def test_read_online_sends_type1_and_parses_reply():
    # Arrange
    info = _reward_info_body(TYPE_ONLINE, 120, res_list=[_reward(1, 999)])
    c, fake = _client({CMD_REWARD_INFO: lambda _b: [s2c(CMD_REWARD_INFO, info)]})
    try:
        # Act
        r = read_online(c)
        # Assert
        assert r.type == 1 and r.res_list == [(1, 999)]
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_REWARD_INFO]
        assert codec.walk_dict(sent[0]) == {1: 1}  # reward_info_c2s {type=1}
    finally:
        c.close()


def test_claim_round_trip_sends_given_type():
    # Arrange
    c, fake = _client({CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")]})
    try:
        # Act
        res = claim(c, TYPE_OFFLINE)
        # Assert
        assert isinstance(res, ClaimResult)
        assert res.success is True and res.type == 2
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CLAIM_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 2}  # claim_reward_c2s {type=2}
    finally:
        c.close()


# --- claim_online: claim only when non-empty -------------------------------

def test_claim_online_claims_when_non_empty():
    # Arrange
    info = _reward_info_body(TYPE_ONLINE, 60, res_list=[_reward(1, 5)])
    c, fake = _client({
        CMD_REWARD_INFO: lambda _b: [s2c(CMD_REWARD_INFO, info)],
        CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")],
    })
    try:
        # Act
        res = claim_online(c)
        # Assert
        assert res is not None and res.success is True and res.type == 1
        claim_cmds = [cmd for _sid, cmd, _b in fake.framed_sent()
                      if cmd == CMD_CLAIM_REWARD]
        assert claim_cmds == [CMD_CLAIM_REWARD]  # exactly one claim sent
    finally:
        c.close()


def test_claim_online_skips_claim_when_empty():
    # Arrange
    info = _reward_info_body(TYPE_ONLINE, 0)  # nothing to claim
    c, fake = _client({
        CMD_REWARD_INFO: lambda _b: [s2c(CMD_REWARD_INFO, info)],
        CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")],
    })
    try:
        # Act
        res = claim_online(c)
        # Assert
        assert res is None
        claim_cmds = [cmd for _sid, cmd, _b in fake.framed_sent()
                      if cmd == CMD_CLAIM_REWARD]
        assert claim_cmds == []  # no claim sent
    finally:
        c.close()


# --- offline: from a received push body, or a direct claim(2) ---------------

def test_claim_offline_from_push_claims_type2_when_non_empty():
    # Arrange
    push = _reward_info_body(TYPE_OFFLINE, 7200, res_list=[_reward(1, 8000)])
    c, fake = _client({CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")]})
    try:
        # Act
        res = claim_offline_from_push(c, push)
        # Assert
        assert res is not None and res.success is True and res.type == 2
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CLAIM_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 2}
    finally:
        c.close()


def test_claim_offline_from_push_skips_empty_push():
    # Arrange
    push = _reward_info_body(TYPE_OFFLINE, 0)  # empty offline reward
    c, fake = _client({CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")]})
    try:
        # Act
        res = claim_offline_from_push(c, push)
        # Assert
        assert res is None
        assert [cmd for _sid, cmd, _b in fake.framed_sent()
                if cmd == CMD_CLAIM_REWARD] == []
    finally:
        c.close()


# --- quick 2h income (主頁掛機彈窗左鈕 btnAd) via ad.ad_reward 0x1602 ----------
# Live (小寶 2026-06-11): tx ad_reward_c2s {config_id#1:4, is_free#3:1} → server
# grants directly (no ad SDK); replies ad_reward_s2c {new_ad#1:p_ad}. Declines
# (30min冷卻 / 一天3次上限) arrive as 0x0201 {error_code#1}.

def _p_ad(id_=4, count=2):
    return codec.pb_msg(1, codec.pb_uint(1, id_) + codec.pb_uint(2, count))


def test_build_ad_reward_body_has_config_id_and_is_free():
    # Arrange / Act
    body = build_ad_reward_body()
    # Assert
    assert codec.walk_dict(body) == {1: AD_QUICK_2H_CONFIG_ID, 3: 1}


def test_claim_quick_2h_success_round_trip():
    # Arrange
    c, fake = _client({CMD_AD_REWARD: lambda _b: [s2c(CMD_AD_REWARD, _p_ad())]})
    try:
        # Act
        res = claim_quick_2h(c)
        # Assert
        assert isinstance(res, QuickClaimResult)
        assert res.success is True and res.error_code is None
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert codec.walk_dict(sent[0]) == {1: AD_QUICK_2H_CONFIG_ID, 3: 1}
    finally:
        c.close()


def test_claim_quick_2h_declined_by_0201_returns_error_code():
    # Arrange — cooldown / daily-cap decline comes back as 0x0201 {error_code#1}
    c, _fake = _client({CMD_AD_REWARD: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 90))]})
    try:
        # Act
        res = claim_quick_2h(c)
        # Assert
        assert res.success is False and res.error_code == 90
    finally:
        c.close()


def test_claim_quick_2h_timeout_returns_failure():
    # Arrange — server never replies
    c, _fake = _client({})
    try:
        # Act
        res = claim_quick_2h(c, timeout=0.2)
        # Assert
        assert res.success is False and res.error_code is None
    finally:
        c.close()


def test_claim_offline_direct_sends_type2():
    # Arrange — no push available, claim(2) directly (server keeps a pending one)
    c, fake = _client({CMD_CLAIM_REWARD: lambda _b: [s2c(CMD_CLAIM_REWARD, b"")]})
    try:
        # Act
        res = claim_offline(c)
        # Assert
        assert res.success is True and res.type == 2
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_CLAIM_REWARD]
        assert codec.walk_dict(sent[0]) == {1: 2}
    finally:
        c.close()

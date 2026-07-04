"""Tests for ws_token.rogue — 萬神試煉 (roguelike, module 76) 本周積分獎勵領取.

Protocol LIVE-captured (小寶 7fe98fc6, CDP 9226, 2026-06-13) and cross-checked
against docs/protocol/ROGUE_PROTO_SCHEMA.json:
  rogue_week_reward_c2s  19482 (0x4C1A)  c2s {}            (empty body)
  rogue_week_reward_s2c  19482           s2c { get_list#1:uint32[],
                                               reward_list#2:p_reward[] }
  p_reward                                   { gtid#1:int32, num#2:int64 }

s2c has NO code field: any 19482 reply is success; an empty get_list just means
"nothing left to claim this week" (still success). Failure arrives on 0x0201.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.rogue import (  # noqa: E402
    CMD_ERROR,
    CMD_WEEK_REWARD,
    WeekRewardResult,
    claim_week_reward,
    parse_week_reward,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- constants --------------------------------------------------------------

def test_cmd_id_matches_schema():
    assert CMD_WEEK_REWARD == 19482       # 0x4C1A rogue.rogue_week_reward
    assert CMD_ERROR == 0x0201


# --- body builders / wire shape ---------------------------------------------

def _p_reward(gtid: int, num: int) -> bytes:
    return codec.pb_msg(2, codec.pb_uint(1, gtid) + codec.pb_uint(2, num))


def _week_reward_body(get_list, rewards) -> bytes:
    """Build a rogue_week_reward_s2c body from get_list + [(gtid, num)]."""
    out = b"".join(codec.pb_uint(1, i) for i in get_list)
    out += b"".join(_p_reward(g, n) for g, n in rewards)
    return out


# --- s2c parsing ------------------------------------------------------------

def test_parse_week_reward_decodes_get_list_and_rewards():
    # The exact LIVE-captured payload: claimed milestones 1..10, four rewards.
    body = _week_reward_body(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        [(1501, 200), (2, 1800), (1291, 1), (1, 10000)],
    )
    r = parse_week_reward(CMD_WEEK_REWARD, body)
    assert r.success is True
    assert r.claimed == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert r.rewards == {1501: 200, 2: 1800, 1291: 1, 1: 10000}
    assert r.error_code is None


def test_parse_week_reward_empty_is_success_nothing_claimed():
    # Already claimed this week: server still replies on 19482 with empty lists.
    r = parse_week_reward(CMD_WEEK_REWARD, b"")
    assert r.success is True
    assert r.claimed == ()
    assert r.rewards == {}


def test_parse_week_reward_error_frame_is_failure():
    body = codec.pb_uint(1, 173)  # error.error_info_s2c {error_code#1}
    r = parse_week_reward(CMD_ERROR, body)
    assert r.success is False
    assert r.error_code == 173
    assert r.rewards == {}


# --- end-to-end over a scripted fake client ---------------------------------

def _client(responder) -> tuple[WSGameClient, FakeTransport]:
    fake = FakeTransport(responder)
    client = WSGameClient(
        CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False
    )
    client.connect()
    return client, fake


def test_claim_week_reward_sends_empty_body_and_parses_reply():
    reward_body = _week_reward_body([1, 2, 3], [(1501, 200), (1, 10000)])
    responder = login_responder(
        {CMD_WEEK_REWARD: lambda body: [s2c(CMD_WEEK_REWARD, reward_body)]}
    )
    client, fake = _client(responder)
    try:
        r = claim_week_reward(client)
    finally:
        client.close()

    assert isinstance(r, WeekRewardResult)
    assert r.success is True
    assert r.claimed == (1, 2, 3)
    assert r.rewards == {1501: 200, 1: 10000}
    # The request MUST be cmd 19482 with an empty body (no fields).
    sent = [(cmd, body) for _sid, cmd, body in fake.framed_sent()
            if cmd == CMD_WEEK_REWARD]
    assert sent == [(CMD_WEEK_REWARD, b"")]


def test_claim_week_reward_error_reply_is_failure():
    responder = login_responder(
        {CMD_WEEK_REWARD: lambda body: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]}
    )
    client, _fake = _client(responder)
    try:
        r = claim_week_reward(client)
    finally:
        client.close()

    assert r.success is False
    assert r.error_code == 173
    assert r.rewards == {}

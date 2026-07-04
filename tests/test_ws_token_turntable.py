"""Tests for ws_token.turntable — 轉盤金幣 (ad-wheel) over pure WS.

Schemas are the live-exported truth (docs/protocol/AD_PROTO_SCHEMA.json,
ad module 22, c2s bodies are empty both directions):
  ad_wheel_info_s2c { cd#1:uint32, num#2:uint32 }   <- cd FIRST, then num
  ad_wheel_spin_s2c { id#1:uint32 }                 <- winning slot id
where num = remaining free/accumulated spins, cd = cooldown expiry timestamp,
and id-1 = configTurntable index (prize content lives in client config, not WS).
Spinning is win-on-spin: there is no separate claim step.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.ad_reward import CMD_AD_INFO, CMD_AD_REWARD  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.turntable import (  # noqa: E402
    AD_TURNTABLE_CONFIG_ID,
    CMD_ERROR,
    CMD_INFO,
    CMD_SPIN,
    TurntableInfo,
    parse_info,
    parse_spin,
    read_info,
    run_daily,
    spin_all_free,
    spin_once,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- parse_info: ad_wheel_info_s2c {cd#1, num#2} — cd FIRST -----------------

def test_parse_info_reads_cd_then_num_in_order():
    # cd is field 1, num is field 2 (NOT num-first)
    body = codec.pb_uint(1, 1781020252) + codec.pb_uint(2, 3)
    info = parse_info(body)
    assert isinstance(info, TurntableInfo)
    assert info.cd == 1781020252
    assert info.num == 3


def test_parse_info_zero_num_means_no_spins_left():
    body = codec.pb_uint(1, 0) + codec.pb_uint(2, 0)
    info = parse_info(body)
    assert info.num == 0
    assert info.cd == 0


def test_parse_info_empty_body_is_all_zero():
    info = parse_info(b"")
    assert info == TurntableInfo(num=0, cd=0)


# --- parse_spin: ad_wheel_spin_s2c {id#1} ----------------------------------

def test_parse_spin_returns_winning_slot_id():
    assert parse_spin(codec.pb_uint(1, 5)) == 5


def test_parse_spin_empty_body_is_zero():
    assert parse_spin(b"") == 0


# --- request bodies are empty ----------------------------------------------

def test_read_info_sends_empty_body():
    body = codec.pb_uint(1, 100) + codec.pb_uint(2, 2)
    c, fake = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        read_info(c)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_INFO]
        assert sent == [b""]
    finally:
        c.close()


def test_spin_once_sends_empty_body():
    c, fake = _client({CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 4))]})
    try:
        spin_once(c)
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_SPIN]
        assert sent == [b""]
    finally:
        c.close()


# --- read_info / spin_once roundtrip ---------------------------------------

def test_read_info_roundtrip():
    body = codec.pb_uint(1, 1781) + codec.pb_uint(2, 7)
    c, _ = _client({CMD_INFO: lambda _b: [s2c(CMD_INFO, body)]})
    try:
        info = read_info(c)
        assert info.num == 7 and info.cd == 1781
    finally:
        c.close()


def test_spin_once_returns_winning_id():
    c, _ = _client({CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 6))]})
    try:
        assert spin_once(c) == 6
    finally:
        c.close()


def test_spin_once_returns_none_on_0x0201_decline():
    # Live (小寶 2026-06-09): a spin can be answered with 0x0201 (code 173,
    # 轉盤需看廣告) instead of 0x1604. spin_once must return None, not crash.
    c, _ = _client({CMD_SPIN: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))]})
    try:
        assert spin_once(c) is None
    finally:
        c.close()


# --- spin_all_free: drain num>0, stop at num<=0 or max_spins ----------------

def test_spin_all_free_spins_exactly_num_times():
    # info says num=3 -> spin 3 times, then stop (no re-read needed)
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 3))],
        CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 2))],
    })
    try:
        out = spin_all_free(c, spacing=0)
        assert out["spun"] == 3
        assert out["results"] == [2, 2, 2]
        spins = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_SPIN]
        assert len(spins) == 3
    finally:
        c.close()


def test_spin_all_free_stops_on_decline_without_hammering():
    # num=5 but the server declines every spin (0x0201 code 173) -> stop after the
    # FIRST attempt, do not re-try (would just waste spins / spam timeouts).
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 5))],
        CMD_SPIN: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 173))],
    })
    try:
        out = spin_all_free(c, spacing=0)
        assert out["spun"] == 0
        assert out["results"] == []
        assert out["declined"] is True
        spins = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_SPIN]
        assert len(spins) == 1   # only ONE attempt before stopping
    finally:
        c.close()


def test_spin_all_free_does_not_spin_when_num_zero():
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 1781) + codec.pb_uint(2, 0))],
        CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 1))],
    })
    try:
        out = spin_all_free(c, spacing=0)
        assert out["spun"] == 0
        assert out["results"] == []
        spins = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_SPIN]
        assert spins == []
    finally:
        c.close()


def test_spin_all_free_stops_at_max_spins():
    # num is huge; max_spins caps the loop
    c, fake = _client({
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 9999))],
        CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 3))],
    })
    try:
        out = spin_all_free(c, spacing=0, max_spins=5)
        assert out["spun"] == 5
        assert out["results"] == [3, 3, 3, 3, 3]
        spins = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_SPIN]
        assert len(spins) == 5
    finally:
        c.close()


# --- run_daily: claim today's ad-funded spins (config 13) then spin ----------
# Live-verified (5554, 2026-06-21): ad_reward(13, is_free=1) bumps ad_wheel_info
# .num by 1 (claim GRANTS a spin), then 0x1604 consumes it. So run_daily banks
# the daily ad top-ups into the wheel pool, then drains via spin_all_free.

_FUTURE = 9_999_999_999  # next_ts far in the future = cooldown engaged


def _ad_entry(config_id, count, next_ts=0):
    """ad_info_s2c ad_list#1 = {config_id#1, count#2, _#3, next_ts#4}."""
    return codec.pb_msg(1, codec.pb_uint(1, config_id)
                        + codec.pb_uint(2, count) + codec.pb_uint(4, next_ts))


def _ad_reward_reply(config_id, count, next_ts):
    """on_ad_reward_s2c {new_ad#1: {config_id#1, count#2, _#3, next_ts#4}}."""
    inner = (codec.pb_uint(1, config_id) + codec.pb_uint(2, count)
             + codec.pb_uint(4, next_ts))
    return codec.pb_msg(1, inner)


def test_run_daily_claims_one_ad_spin_then_spins_the_pool():
    # ad[13] count=0 -> claim 1 (server returns count=1 + future next_ts so the
    # 2nd is cooldown-skipped this session), which banks +1 into the wheel; then
    # spin_all_free spins the available turn.
    c, fake = _client({
        CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, _ad_entry(AD_TURNTABLE_CONFIG_ID, 0))],
        CMD_AD_REWARD: lambda _b: [s2c(CMD_AD_REWARD,
                                       _ad_reward_reply(AD_TURNTABLE_CONFIG_ID, 1, _FUTURE))],
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 1))],
        CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 4))],
    })
    try:
        out = run_daily(c, spacing=0)
        assert out["ad_topup"]["claimed"] == 1
        assert out["spun"] == 1
        assert out["results"] == [4]
        rewards = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert len(rewards) == 1   # claimed exactly once (cooldown gates the 2nd)
    finally:
        c.close()


def test_run_daily_skips_ad_claim_when_capped_but_still_spins():
    # ad[13] count=2 == cap -> NO 0x1602 sent; free spins still drained.
    c, fake = _client({
        CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, _ad_entry(AD_TURNTABLE_CONFIG_ID, 2))],
        CMD_INFO: lambda _b: [s2c(CMD_INFO, codec.pb_uint(1, 0) + codec.pb_uint(2, 2))],
        CMD_SPIN: lambda _b: [s2c(CMD_SPIN, codec.pb_uint(1, 3))],
    })
    try:
        out = run_daily(c, spacing=0)
        assert "maxed" in out["ad_topup"].get("skipped", "")
        assert out["spun"] == 2
        rewards = [cmd for _sid, cmd, _b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert rewards == []   # never sent a claim past the cap
    finally:
        c.close()


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake

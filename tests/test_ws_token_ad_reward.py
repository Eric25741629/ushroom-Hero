"""Tests for ws_token.ad_reward — 看廣告獎勵純 WS 自動領取.

ALL "watch ad to get X" buttons go through ONE generic cmd (live-verified 5556
web_h5 CDP 9223, 2026-06-19; memory reference_ws_ad_reward_protocol):

  ad_info   0x1601 (5633)  c2s {}  (empty)
                           -> s2c repeated field1 = {config_id#1, count#2,
                                                     _#3, next_ts#4}
  ad_reward 0x1602 (5634)  c2s {config_id#1, is_free#3=1}  (ext#2 omitted)
                           -> s2c 0x1602 {1: {config_id#1, count#2, _#3, next_ts#4}}
                           -> 0x0201 {error_code#1} on reject (daily-limit = 89)

Because the account bought the NO_ADS privilege, is_free=1 grants the reward
instantly (no video). The discipline mirrors farm shop "read today's count →
claim only the deficit → skip at the cap": read counts once, then for each
config_id claim only up to ``TIMES`` and never when ``next_ts`` is still in the
future (cooldown). A config_id absent from the s2c counts as count=0.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.ad_reward import (  # noqa: E402
    AD_NAMES,
    CMD_AD_INFO,
    CMD_AD_REWARD,
    CMD_ERROR,
    DEFAULT_CONFIG_IDS,
    IS_FREE,
    TIMES,
    AdClaimResult,
    build_ad_reward_body,
    claim_ad,
    claim_ads,
    claim_once,
    read_ad_counts,
)
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- body builder -----------------------------------------------------------

def test_build_ad_reward_body_is_id_then_is_free():
    # config_id is field#1 (varint), is_free is field#3 (varint); ext#2 omitted.
    body = build_ad_reward_body(14)
    assert body == codec.pb_uint(1, 14) + codec.pb_uint(3, IS_FREE)


def test_build_ad_reward_body_honours_is_free_arg():
    body = build_ad_reward_body(15, is_free=0)
    assert body == codec.pb_uint(1, 15) + codec.pb_uint(3, 0)


# --- constants --------------------------------------------------------------

def test_default_config_ids_and_times_and_names():
    assert DEFAULT_CONFIG_IDS == [1, 2, 3, 12, 14, 15]
    assert TIMES[12] == 3 and TIMES[14] == 5 and TIMES[15] == 2
    assert AD_NAMES[12] and AD_NAMES[14] and AD_NAMES[15]


# --- read_ad_counts: repeated field1 parse ----------------------------------

def _info_body(*entries) -> bytes:
    """Build an ad_info_s2c body: repeated field1 = {config_id#1, count#2, next_ts#4}."""
    out = b""
    for cid, count, next_ts in entries:
        sub = (codec.pb_uint(1, cid) + codec.pb_uint(2, count)
               + codec.pb_uint(4, next_ts))
        out += codec.pb_msg(1, sub)
    return out


def test_read_ad_counts_parses_repeated_field1():
    body = _info_body((14, 2, 0), (15, 1, 1781000000))
    c, fake = _client({CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, body)]})
    try:
        counts = read_ad_counts(c)
        assert counts[14] == {"count": 2, "next_ts": 0}
        assert counts[15] == {"count": 1, "next_ts": 1781000000}
        # request body is empty
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_AD_INFO]
        assert sent == [b""]
    finally:
        c.close()


def test_read_ad_counts_missing_id_is_absent_treated_as_zero():
    # config 12 is NOT in the s2c list (same shop_info convention); callers
    # default missing ids to count=0.
    body = _info_body((14, 5, 0))
    c, _ = _client({CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, body)]})
    try:
        counts = read_ad_counts(c)
        assert 12 not in counts
        assert counts.get(12, {}).get("count", 0) == 0
    finally:
        c.close()


def test_read_ad_counts_empty_body_is_empty_dict():
    c, _ = _client({CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, b"")]})
    try:
        assert read_ad_counts(c) == {}
    finally:
        c.close()


# --- claim_once: 0x1602 success / 0x0201 reject -----------------------------

def _reward_body(cid: int, count: int, next_ts: int) -> bytes:
    """ad_reward_s2c {1: {config_id#1, count#2, next_ts#4}}."""
    inner = (codec.pb_uint(1, cid) + codec.pb_uint(2, count)
             + codec.pb_uint(4, next_ts))
    return codec.pb_msg(1, inner)


def test_claim_once_success_parses_new_count_and_next_ts():
    body = _reward_body(14, 1, 1781000300)
    c, fake = _client({CMD_AD_REWARD: lambda _b: [s2c(CMD_AD_REWARD, body)]})
    try:
        r = claim_once(c, 14)
        assert isinstance(r, AdClaimResult)
        assert r.success is True
        assert r.response_cmd == CMD_AD_REWARD
        assert r.new_count == 1
        assert r.next_ts == 1781000300
        assert r.error_code is None
        # sent the id+is_free body
        sent = [b for _sid, cmd, b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert sent == [build_ad_reward_body(14)]
    finally:
        c.close()


def test_claim_once_reject_on_0x0201_records_error_code():
    c, _ = _client({CMD_AD_REWARD: lambda _b: [s2c(CMD_ERROR, codec.pb_uint(1, 89))]})
    try:
        r = claim_once(c, 14)
        assert r.success is False
        assert r.response_cmd == CMD_ERROR
        assert r.error_code == 89
    finally:
        c.close()


# --- claim_ad: cap + cooldown gating ----------------------------------------

def test_claim_ad_skips_without_request_when_maxed():
    # count already at TIMES[15]=2 -> remaining 0 -> NO packet sent.
    counts = {15: {"count": 2, "next_ts": 0}}
    c, fake = _client({})
    try:
        out = claim_ad(c, 15, counts=counts, now=1_000_000)
        assert "skipped" in out
        assert "maxed" in out["skipped"]
        # no ad_reward packet sent
        assert all(cmd != CMD_AD_REWARD for _s, cmd, _b in fake.framed_sent())
    finally:
        c.close()


def test_claim_ad_skips_without_request_when_in_cooldown():
    # next_ts in the future -> cooldown -> NO packet sent.
    counts = {14: {"count": 1, "next_ts": 2_000_000}}
    c, fake = _client({})
    try:
        out = claim_ad(c, 14, counts=counts, now=1_000_000)
        assert "skipped" in out
        assert "cooldown" in out["skipped"]
        assert all(cmd != CMD_AD_REWARD for _s, cmd, _b in fake.framed_sent())
    finally:
        c.close()


def test_claim_ad_loops_until_remaining_zero():
    # config 12 (cd=0): TIMES=3, start count=0. Each success bumps count and
    # returns next_ts=0 (no cooldown), so it loops to the daily cap.
    state = {"count": 0}

    def _resp(_b):
        state["count"] += 1
        return [s2c(CMD_AD_REWARD, _reward_body(12, state["count"], 0))]

    counts = {}  # 12 absent -> treated as count 0
    c, fake = _client({CMD_AD_REWARD: _resp})
    try:
        out = claim_ad(c, 12, counts=counts, now=1_000_000)
        assert out["name"] == AD_NAMES[12]
        assert out["claimed"] == TIMES[12]  # 3 claims
        claims = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert len(claims) == TIMES[12]
    finally:
        c.close()


def test_claim_ad_stops_on_0x0201():
    # First claim succeeds (next_ts=0, more remaining), second is rejected -> stop.
    seq = {"n": 0}

    def _resp(_b):
        seq["n"] += 1
        if seq["n"] == 1:
            return [s2c(CMD_AD_REWARD, _reward_body(12, 1, 0))]
        return [s2c(CMD_ERROR, codec.pb_uint(1, 89))]

    c, fake = _client({CMD_AD_REWARD: _resp})
    try:
        out = claim_ad(c, 12, counts={}, now=1_000_000)
        assert out["claimed"] == 1
        assert "error_code=89" in str(out["stopped"])
        claims = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert len(claims) == 2  # one success + one rejected attempt
    finally:
        c.close()


def test_claim_ad_stops_when_server_returns_future_next_ts():
    # config 14 has cd=300; after one claim the server returns a future next_ts,
    # so claim_ad must stop (don't hammer the cooldown) even with remaining>0.
    def _resp(_b):
        return [s2c(CMD_AD_REWARD, _reward_body(14, 1, 9_000_000))]

    c, fake = _client({CMD_AD_REWARD: _resp})
    try:
        out = claim_ad(c, 14, counts={}, now=1_000_000)
        assert out["claimed"] == 1
        claims = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_AD_REWARD]
        assert len(claims) == 1  # stopped after the cooldown kicked in
    finally:
        c.close()


# --- claim_ads: aggregate ---------------------------------------------------

def test_claim_ads_reads_counts_once_and_aggregates():
    # 14 at cap (count 5/5 skip), 15 claimable once, 12 absent -> claimable.
    info = _info_body((14, 5, 0), (15, 1, 0))
    _FUTURE = 9_999_999_999  # far past any real time.time() -> cooldown trips

    def _ad_resp(body):
        cid = codec.walk_dict(body).get(1)
        return [s2c(CMD_AD_REWARD, _reward_body(cid, 1, _FUTURE))]

    c, fake = _client({
        CMD_AD_INFO: lambda _b: [s2c(CMD_AD_INFO, info)],
        CMD_AD_REWARD: _ad_resp,
    })
    try:
        out = claim_ads(c, [12, 14, 15], timeout=1.0)
        # 14 maxed -> skipped (no claim); 12 + 15 claimed once each
        assert "maxed" in out["results"][AD_NAMES[14]]["skipped"]
        assert out["results"][AD_NAMES[12]]["claimed"] == 1
        assert out["results"][AD_NAMES[15]]["claimed"] == 1
        assert out["total_claimed"] == 2
        # ad_info read exactly once (counts reused across the per-id claims)
        infos = [cmd for _s, cmd, _b in fake.framed_sent() if cmd == CMD_AD_INFO]
        assert len(infos) == 1
    finally:
        c.close()


def test_claim_ads_degrades_to_zero_counts_when_info_read_fails():
    # ad_info times out (no responder) -> counts={} (all zero) but each id is
    # still attempted. Each claim returns a future next_ts so one claim per id.
    def _ad_resp(body):
        cid = codec.walk_dict(body).get(1)
        return [s2c(CMD_AD_REWARD, _reward_body(cid, 1, 9_999_999_999))]

    c, _ = _client({CMD_AD_REWARD: _ad_resp})
    try:
        out = claim_ads(c, [15], timeout=0.3)
        assert out["results"][AD_NAMES[15]]["claimed"] == 1
        assert out["total_claimed"] == 1
    finally:
        c.close()


# --- runner: _run_ad_rewards enabled / disabled -----------------------------

def test_run_ad_rewards_disabled_self_skips_without_request():
    from ws_token import runner

    out = runner._run_ad_rewards(object(), config_ids=(12, 14, 15), enabled=False)
    assert "skipped" in out


def test_run_ad_rewards_empty_ids_self_skips():
    from ws_token import runner

    out = runner._run_ad_rewards(object(), config_ids=(), enabled=True)
    assert "skipped" in out


def test_run_ad_rewards_enabled_calls_claim_ads(monkeypatch):
    from ws_token import runner

    seen = {}

    def fake_claim_ads(client, config_ids, **k):
        seen["ids"] = list(config_ids)
        return {"results": {}, "total_claimed": 7}

    monkeypatch.setattr(runner.ad_reward, "claim_ads", fake_claim_ads)

    out = runner._run_ad_rewards(object(), config_ids=(12, 15), enabled=True)
    assert seen["ids"] == [12, 15]
    assert out["total_claimed"] == 7


def test_ad_rewards_in_task_order_after_idle_before_turntable():
    from ws_token import runner

    order = list(runner.TASK_ORDER)
    assert order.index("idle_reward") < order.index("ad_rewards")
    assert order.index("ad_rewards") < order.index("turntable")


def test_run_ad_rewards_threads_device_into_claim_ads(monkeypatch):
    """device 必須傳給 claim_ads(device_id=)，否則領取細節落不到 per-device log。"""
    from ws_token import runner

    seen = {}

    def fake_claim_ads(client, config_ids, **k):
        seen["device_id"] = k.get("device_id")
        return {"results": {}, "total_claimed": 0}

    monkeypatch.setattr(runner.ad_reward, "claim_ads", fake_claim_ads)
    runner._run_ad_rewards(object(), config_ids=(1, 2, 3), enabled=True,
                           device="emulator-5554")
    assert seen["device_id"] == "emulator-5554"


# --- per-device logger routing ----------------------------------------------

def test_resolve_logger_falls_back_to_module_logger_without_device():
    import ws_token.ad_reward as ar

    assert ar._resolve_logger(None) is ar.logger
    assert ar._resolve_logger("") is ar.logger


def test_resolve_logger_uses_per_device_factory_when_device_given(monkeypatch):
    import ws_token.ad_reward as ar
    import utils.logging_utils as lu

    sentinel = object()
    seen = {}

    def fake_factory(device_id):
        seen["device_id"] = device_id
        return sentinel

    monkeypatch.setattr(lu, "get_or_create_ws_ad_reward_logger", fake_factory)
    assert ar._resolve_logger("emulator-5560") is sentinel
    assert seen["device_id"] == "emulator-5560"


# --- helpers ----------------------------------------------------------------

def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake),
                     heartbeat_enabled=False)
    c.connect()
    return c, fake

"""Tests for ws_token.mail + game_actions.mail_scheduler — daily mail-claim.

Schemas are LIVE-verified on emulator-5554 2026-06-14
(docs/protocol/MAIL_PROTO_SCHEMA.md):

  mail_list  5377 (0x1501)  c2s { mail_id#1:uint64 }   ⚠ mail_id REQUIRED
                            s2c { mail_list#1:p_mail[] }
  mail_claim 5380 (0x1504)  c2s { mail_id#1:uint64, type#2:uint32 }  mail_id=0 = all
                            s2c { claim_list#1:uint64[], goods_list#2:p_reward[] }
                                 (無可領回空 body — idempotent)
  p_mail { id#1, cfg_id#2, ?#3, title#5, content#6, send_time#7, exp_time#8,
           is_read#9, is_attach#10, goods_list#11:p_reward[] }

Capacity caps: client has NO hard cap for 武魂 (skill) or 神器附魔寶石 (gem) — the
helpers only read current counts + a best-effort threshold check.
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import codec  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token import mail  # noqa: E402
from game_actions import mail_scheduler  # noqa: E402
from tests.fakes.ws_fakes import (  # noqa: E402
    CREDS,
    FakeTransport,
    factory_for,
    login_responder,
    s2c,
)


# --- helpers to build wire bodies -------------------------------------------

def _reward(item_id, num):
    # p_reward {item_id#1, num#2}
    return codec.pb_uint(1, item_id) + codec.pb_uint(2, num)


def _mail(mail_id, cfg_id=10000, send_time=100, exp_time=200, is_read=1,
          is_attach=1, goods=()):
    return (codec.pb_uint(1, mail_id) + codec.pb_uint(2, cfg_id)
            + codec.pb_uint(7, send_time) + codec.pb_uint(8, exp_time)
            + codec.pb_uint(9, is_read) + codec.pb_uint(10, is_attach)
            + b"".join(codec.pb_msg(11, g) for g in goods))


def _mail_list_body(mails):
    return b"".join(codec.pb_msg(1, m) for m in mails)


def _claim_s2c_body(claim_ids=(), goods=()):
    return (b"".join(codec.pb_uint(1, i) for i in claim_ids)
            + b"".join(codec.pb_msg(2, g) for g in goods))


def _client(extra):
    fake = FakeTransport(login_responder(extra))
    c = WSGameClient(CREDS, transport_factory=factory_for(fake), heartbeat_enabled=False)
    c.connect()
    return c, fake


# --- body builders ----------------------------------------------------------

def test_build_list_body_carries_mail_id():
    # ⚠ live: empty body returns empty; mail_id is required.
    assert codec.walk_dict(mail.build_list_body(0)) == {1: 0}
    assert codec.walk_dict(mail.build_list_body(42)) == {1: 42}


def test_build_claim_body_is_mail_id_and_type():
    assert codec.walk_dict(mail.build_claim_body(0, 0)) == {1: 0, 2: 0}
    assert codec.walk_dict(mail.build_claim_body(99, 0)) == {1: 99, 2: 0}


# --- parse_mail_list --------------------------------------------------------

def test_parse_mail_list_decodes_fields_and_goods():
    body = _mail_list_body([
        _mail(111, cfg_id=10251, send_time=1780841105, exp_time=1783433105,
              is_read=1, is_attach=1, goods=[_reward(2, 2000), _reward(201, 15)]),
    ])
    mails = mail.parse_mail_list(body)
    assert len(mails) == 1
    m = mails[0]
    assert m.mail_id == 111
    assert m.cfg_id == 10251
    assert m.send_time == 1780841105
    assert m.exp_time == 1783433105
    assert m.is_read is True
    assert m.is_attach is True
    assert m.goods_list == [(2, 2000), (201, 15)]


def test_parse_mail_list_empty_body_is_empty():
    # The live "no body" reply is 0 bytes -> no mails.
    assert mail.parse_mail_list(b"") == []


def test_claimable_mail_filters_is_attach():
    body = _mail_list_body([
        _mail(1, is_attach=1),
        _mail(2, is_attach=0),
        _mail(3, is_attach=1),
    ])
    mails = mail.parse_mail_list(body)
    claimable = mail.claimable_mail(mails)
    assert [m.mail_id for m in claimable] == [1, 3]


# --- list_mail round-trip ---------------------------------------------------

def test_list_mail_sends_mail_id_zero_and_parses():
    body = _mail_list_body([_mail(7, is_attach=1, goods=[_reward(1001, 100)])])
    c, fake = _client({mail.CMD_MAIL_LIST: lambda _b: [s2c(mail.CMD_MAIL_LIST, body)]})
    try:
        mails = mail.list_mail(c)
        assert [m.mail_id for m in mails] == [7]
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == mail.CMD_MAIL_LIST]
        assert codec.walk_dict(sent[0]) == {1: 0}  # mail_id=0 (all)
    finally:
        c.close()


# --- claim_mail / claim_all_attachments -------------------------------------

def test_claim_mail_single_round_trip():
    reply = _claim_s2c_body(claim_ids=[55], goods=[_reward(1001, 100), _reward(2, 5000)])
    c, fake = _client({mail.CMD_MAIL_CLAIM: lambda _b: [s2c(mail.CMD_MAIL_CLAIM, reply)]})
    try:
        res = mail.claim_mail(c, 55)
        assert res.success is True
        assert res.claimed_ids == [55]
        assert res.goods_list == [(1001, 100), (2, 5000)]
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == mail.CMD_MAIL_CLAIM]
        assert codec.walk_dict(sent[0]) == {1: 55, 2: 0}
    finally:
        c.close()


def test_claim_all_sends_mail_id_zero():
    reply = _claim_s2c_body(claim_ids=[1, 2, 3], goods=[_reward(2, 9000)])
    c, fake = _client({mail.CMD_MAIL_CLAIM: lambda _b: [s2c(mail.CMD_MAIL_CLAIM, reply)]})
    try:
        res = mail.claim_all_attachments(c)
        assert res.success is True
        assert res.claimed_ids == [1, 2, 3]
        assert res.goods_list == [(2, 9000)]
        sent = [b for _s, cmd, b in fake.framed_sent() if cmd == mail.CMD_MAIL_CLAIM]
        assert codec.walk_dict(sent[0]) == {1: 0, 2: 0}  # claim-all
    finally:
        c.close()


def test_claim_all_empty_reply_is_success_nothing_claimed():
    # LIVE: nothing claimable -> empty body on cmd 5380 (no 0x0201).
    c, _fake = _client({mail.CMD_MAIL_CLAIM: lambda _b: [s2c(mail.CMD_MAIL_CLAIM, b"")]})
    try:
        res = mail.claim_all_attachments(c)
        assert res.success is True            # reply arrived on the claim cmd
        assert res.claimed_ids == []
        assert res.goods_list == []
        assert res.error_code is None
    finally:
        c.close()


def test_claim_all_error_channel_returns_error_code():
    c, _fake = _client({mail.CMD_MAIL_CLAIM:
                        lambda _b: [s2c(mail.CMD_ERROR, codec.pb_uint(1, 7))]})
    try:
        res = mail.claim_all_attachments(c)
        assert res.success is False
        assert res.error_code == 7
    finally:
        c.close()


# --- capacity helpers (best-effort; no hard cap in client) ------------------

def _gem_info_body(n):
    # artifact_gem_info_s2c: top-level f2 repeated = each gem; count = n.
    gem = codec.pb_uint(1, 999) + codec.pb_uint(2, 8) + codec.pb_uint(3, 6)
    return codec.pb_uint(3, 5) + b"".join(codec.pb_msg(2, gem) for _ in range(n))


def _skill_list_body(active_n, owned_n):
    # skill_list_s2c: f1 = active skills, f2 = owned skill-items {item_id, count}.
    active = codec.pb_uint(1, 1) + codec.pb_uint(2, 1056) + codec.pb_uint(3, 199)
    owned = codec.pb_uint(1, 2032) + codec.pb_uint(2, 1)
    return (b"".join(codec.pb_msg(1, active) for _ in range(active_n))
            + b"".join(codec.pb_msg(2, owned) for _ in range(owned_n)))


def test_read_gem_count_counts_field2_entries():
    body = _gem_info_body(2515)
    c, _fake = _client({mail.CMD_GEM_INFO: lambda _b: [s2c(mail.CMD_GEM_INFO, body)]})
    try:
        assert mail.read_gem_count(c) == 2515
    finally:
        c.close()


def test_read_skill_count_counts_owned_items_only():
    body = _skill_list_body(active_n=6, owned_n=51)
    c, _fake = _client({mail.CMD_SKILL_LIST: lambda _b: [s2c(mail.CMD_SKILL_LIST, body)]})
    try:
        # owned (f2), NOT active (f1) -> 51
        assert mail.read_skill_count(c) == 51
    finally:
        c.close()


def test_read_gem_count_timeout_returns_none():
    c, _fake = _client({})  # no responder -> timeout
    try:
        assert mail.read_gem_count(c, timeout=0.2) is None
    finally:
        c.close()


def test_is_gem_full_threshold_check():
    body = _gem_info_body(600)
    c, _fake = _client({mail.CMD_GEM_INFO: lambda _b: [s2c(mail.CMD_GEM_INFO, body)]})
    try:
        assert mail.is_gem_full(c, threshold=500) is True
        # re-read with a higher threshold
    finally:
        c.close()
    body2 = _gem_info_body(100)
    c2, _f2 = _client({mail.CMD_GEM_INFO: lambda _b: [s2c(mail.CMD_GEM_INFO, body2)]})
    try:
        assert mail.is_gem_full(c2, threshold=500) is False
    finally:
        c2.close()


def test_is_skill_full_threshold_check():
    body = _skill_list_body(active_n=6, owned_n=80)
    c, _fake = _client({mail.CMD_SKILL_LIST: lambda _b: [s2c(mail.CMD_SKILL_LIST, body)]})
    try:
        assert mail.is_skill_full(c, threshold=80) is True
        assert mail.is_skill_full(c, threshold=81) is False
    finally:
        c.close()


# --- mail_scheduler once-daily gate -----------------------------------------

class _FakeClaimClient:
    """Minimal client stub: records claim_all calls, returns a scripted result."""

    def __init__(self, result):
        self._result = result
        self.claim_calls = 0

    # ws_token.mail.claim_all_attachments calls client.call_for; but the scheduler
    # delegates to mail.claim_all_attachments — patched in the tests below instead.


def _patch_claim(monkeypatch, result, recorder):
    def fake_claim_all(client, **kw):
        recorder.append("claim_all")
        return result
    monkeypatch.setattr(mail_scheduler.mail, "claim_all_attachments", fake_claim_all)


def test_scheduler_claims_when_due_and_writes_date(monkeypatch, tmp_path):
    recorder: list = []
    result = mail.ClaimResult(True, mail.CMD_MAIL_CLAIM,
                              claimed_ids=[1, 2], goods_list=[(1001, 100)])
    _patch_claim(monkeypatch, result, recorder)
    now = datetime(2026, 6, 14, 9, 0, 0)

    out = mail_scheduler.run_mail_claim_if_due(
        object(), device="dev", state_dir=tmp_path, now=now)

    assert out["claimed_run"] is True
    assert out["success"] is True
    assert out["claimed_count"] == 2
    assert recorder == ["claim_all"]
    # date persisted
    from ws_token import state as ws_state
    st = ws_state.load_state("dev", state_dir=tmp_path)
    assert st["mail"]["last_date"] == "2026-06-14"


def test_scheduler_skips_when_already_claimed_today(monkeypatch, tmp_path):
    recorder: list = []
    result = mail.ClaimResult(True, mail.CMD_MAIL_CLAIM)
    _patch_claim(monkeypatch, result, recorder)
    now = datetime(2026, 6, 14, 9, 0, 0)
    # seed today's date
    from ws_token import state as ws_state
    ws_state.save_state("dev", {"mail": {"last_date": "2026-06-14"}}, state_dir=tmp_path)

    out = mail_scheduler.run_mail_claim_if_due(
        object(), device="dev", state_dir=tmp_path, now=now)

    assert out["claimed_run"] is False
    assert "already claimed" in out["reason"]
    assert recorder == []  # no claim sent


def test_scheduler_does_not_write_date_on_failure(monkeypatch, tmp_path):
    recorder: list = []
    result = mail.ClaimResult(False, mail.CMD_ERROR, error_code=7)
    _patch_claim(monkeypatch, result, recorder)
    now = datetime(2026, 6, 14, 9, 0, 0)

    out = mail_scheduler.run_mail_claim_if_due(
        object(), device="dev", state_dir=tmp_path, now=now)

    assert out["claimed_run"] is True
    assert out["success"] is False
    from ws_token import state as ws_state
    st = ws_state.load_state("dev", state_dir=tmp_path)
    assert "mail" not in st  # date NOT written -> retries next wake


def test_scheduler_capacity_advisory_never_blocks_claim(monkeypatch, tmp_path):
    """A gem/skill threshold hit logs a warning but STILL claims (no hard cap)."""
    recorder: list = []
    result = mail.ClaimResult(True, mail.CMD_MAIL_CLAIM, claimed_ids=[5])
    _patch_claim(monkeypatch, result, recorder)
    # gem count over threshold (full) and skill count over threshold (full)
    monkeypatch.setattr(mail_scheduler.mail, "read_gem_count", lambda c, **k: 2515)
    monkeypatch.setattr(mail_scheduler.mail, "read_skill_count", lambda c, **k: 90)
    now = datetime(2026, 6, 14, 9, 0, 0)

    out = mail_scheduler.run_mail_claim_if_due(
        object(), device="dev", gem_threshold=500, skill_threshold=80,
        state_dir=tmp_path, now=now)

    # full was detected BUT the claim still ran
    assert out["capacity"]["gem_full"] is True
    assert out["capacity"]["skill_full"] is True
    assert out["claimed_run"] is True
    assert recorder == ["claim_all"]

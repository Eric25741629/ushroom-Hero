"""Tests for utils.family_lieyan — 烈焰山洞 daily claim."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_page(eval_side_effect=None, eval_return=None):
    page = MagicMock()
    if eval_side_effect is not None:
        page.evaluate.side_effect = eval_side_effect
    else:
        page.evaluate.return_value = eval_return
    return page


# ──────────────────────────────────────────────────────────────────────
# _parse_box_info
# ──────────────────────────────────────────────────────────────────────


def test_parse_box_info_handles_top_level_box_info_list():
    from utils.family_lieyan import _parse_box_info
    data = {
        "box_info": [
            {"type": 1, "got_count": 1, "chest_limit": 1},
            {"type": 2, "got_count": 0, "chest_limit": 1},
            {"type": 3, "got_count": 1, "chest_limit": 1},
            {"type": 4, "got_count": 1, "chest_limit": 1},
        ]
    }
    r = _parse_box_info(data)
    assert r[1]["got_count"] == 1 and r[1]["chest_limit"] == 1
    assert r[2]["got_count"] == 0 and r[2]["chest_limit"] == 1
    assert r[3]["got_count"] == 1
    assert r[4]["got_count"] == 1


def test_parse_box_info_falls_back_to_boxes_key():
    """Some game versions may use 'boxes' instead of 'box_info'."""
    from utils.family_lieyan import _parse_box_info
    data = {"boxes": [{"type": 1, "got_count": 0, "chest_limit": 1}]}
    r = _parse_box_info(data)
    assert 1 in r


def test_parse_box_info_handles_short_field_names():
    """When game uses 'got' / 'limit' instead of long names."""
    from utils.family_lieyan import _parse_box_info
    data = {"box_info": [{"type": 1, "got": 1, "limit": 2}]}
    r = _parse_box_info(data)
    assert r[1] == {"got_count": 1, "chest_limit": 2}


def test_parse_box_info_returns_empty_when_no_known_key():
    from utils.family_lieyan import _parse_box_info
    assert _parse_box_info({"other": []}) == {}
    assert _parse_box_info({}) == {}


def test_parse_box_info_ignores_non_dict_input():
    from utils.family_lieyan import _parse_box_info
    assert _parse_box_info(None) == {}
    assert _parse_box_info([]) == {}
    assert _parse_box_info("string") == {}


# ──────────────────────────────────────────────────────────────────────
# claim_lieyan_daily — high-level flow
# ──────────────────────────────────────────────────────────────────────


def test_claim_returns_no_page_when_page_is_none():
    from utils.family_lieyan import claim_lieyan_daily
    r = claim_lieyan_daily(None)
    assert r["ok"] is False
    assert r["error"] == "no_page"


def test_claim_returns_error_when_install_fails():
    from utils.family_lieyan import claim_lieyan_daily
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("playwright lost connection")
    r = claim_lieyan_daily(page)
    assert r["ok"] is False
    assert "hook_install_failed" in (r.get("error") or "")


def test_claim_returns_error_when_no_info_response():
    """If info send succeeds but no s2c arrives within wait window → error."""
    from utils.family_lieyan import claim_lieyan_daily
    # evals: install, drain(empty), send_info, drain(empty after wait)
    eval_returns = iter(["installed", [], "sent", []])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(eval_returns))
    with patch("utils.family_lieyan.time.sleep"):
        r = claim_lieyan_daily(page)
    assert r["ok"] is False
    assert r["error"] == "no_info_response"


def test_claim_skips_all_when_all_maxed():
    """All 4 types already at limit → 0 claimed, 4 skipped."""
    from utils.family_lieyan import claim_lieyan_daily
    info_payload = {"box_info": [
        {"type": 1, "got_count": 1, "chest_limit": 1},
        {"type": 2, "got_count": 1, "chest_limit": 1},
        {"type": 3, "got_count": 1, "chest_limit": 1},
        {"type": 4, "got_count": 1, "chest_limit": 1},
    ]}
    info_frame = {"ev": "info", "ts": 1, "data": json.dumps(info_payload)}
    eval_returns = iter([
        "installed",   # install hook
        [],            # initial drain (clear)
        "sent",        # send info
        [info_frame],  # drain after info wait → got info
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(eval_returns))
    with patch("utils.family_lieyan.time.sleep"):
        r = claim_lieyan_daily(page)
    assert r["ok"] is True
    assert r["claimed"] == []
    assert len(r["skipped"]) == 4
    assert all("maxed" in reason for _t, reason in r["skipped"])


def test_claim_claims_only_types_under_limit():
    """Type 2 & 4 are under limit; should send 2 claims, get 2 acks."""
    from utils.family_lieyan import claim_lieyan_daily
    info_payload = {"box_info": [
        {"type": 1, "got_count": 1, "chest_limit": 1},  # maxed
        {"type": 2, "got_count": 0, "chest_limit": 1},  # claim
        {"type": 3, "got_count": 1, "chest_limit": 1},  # maxed
        {"type": 4, "got_count": 0, "chest_limit": 1},  # claim
    ]}
    info_frame = {"ev": "info", "data": json.dumps(info_payload)}
    update_frame = {"ev": "update_box", "data": "{}"}
    eval_returns = iter([
        "installed",      # install
        [],               # initial drain
        "sent",           # send info
        [info_frame],     # drain → got info
        # type 1: maxed → skipped (no eval)
        # type 2: drain(before send), send, drain(after wait)
        [],               # drain pre-send
        "sent",           # send claim t=2
        [update_frame],   # drain → got update_box ack
        # type 3: maxed → skipped
        # type 4: drain, send, drain
        [],
        "sent",
        [update_frame],
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(eval_returns))
    with patch("utils.family_lieyan.time.sleep"):
        r = claim_lieyan_daily(page)
    assert r["ok"] is True
    assert sorted(r["claimed"]) == [2, 4]
    assert len(r["skipped"]) == 2
    # Both skipped reasons should mention "maxed"
    for _t, reason in r["skipped"]:
        assert "maxed" in reason


def test_claim_reports_no_ack_as_skip():
    """If claim is sent but server doesn't reply with update_box → skip with reason."""
    from utils.family_lieyan import claim_lieyan_daily
    info_payload = {"box_info": [
        {"type": 1, "got_count": 0, "chest_limit": 1},
        {"type": 2, "got_count": 1, "chest_limit": 1},
        {"type": 3, "got_count": 1, "chest_limit": 1},
        {"type": 4, "got_count": 1, "chest_limit": 1},
    ]}
    info_frame = {"ev": "info", "data": json.dumps(info_payload)}
    eval_returns = iter([
        "installed",
        [],
        "sent",
        [info_frame],
        [],               # pre-send drain for t=1
        "sent",           # send t=1
        [],               # drain — no ack arrives
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(eval_returns))
    with patch("utils.family_lieyan.time.sleep"):
        r = claim_lieyan_daily(page)
    assert r["claimed"] == []
    # type 1 should be skipped as no_update_box_ack
    t1_skip = next((reason for t, reason in r["skipped"] if t == 1), None)
    assert t1_skip == "no_update_box_ack"


def test_claim_handles_type_missing_from_box_info():
    """If server returns only 2 box_info entries (type 1+2), types 3+4 → skipped as not_in_box_info."""
    from utils.family_lieyan import claim_lieyan_daily
    info_payload = {"box_info": [
        {"type": 1, "got_count": 1, "chest_limit": 1},
        {"type": 2, "got_count": 1, "chest_limit": 1},
    ]}
    info_frame = {"ev": "info", "data": json.dumps(info_payload)}
    eval_returns = iter([
        "installed", [], "sent", [info_frame],
    ])
    page = _make_page(eval_side_effect=lambda *a, **kw: next(eval_returns))
    with patch("utils.family_lieyan.time.sleep"):
        r = claim_lieyan_daily(page)
    assert r["claimed"] == []
    reasons = {t: reason for t, reason in r["skipped"]}
    assert reasons[3] == "not_in_box_info"
    assert reasons[4] == "not_in_box_info"

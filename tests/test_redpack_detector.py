"""Unit tests for utils.redpack_detector — mock both Playwright page and WS."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


# ──────────────────────────────────────────────────────────────────────
# Protobuf walker — exercise wire format edge cases
# ──────────────────────────────────────────────────────────────────────


def test_walker_decodes_simple_varint():
    from utils.redpack_detector import _walk_pb
    # field 1 (varint), value 42
    out = _walk_pb(bytes([0x08, 0x2a]))
    assert out == [{"field": 1, "wire": 0, "value": 42}]


def test_walker_decodes_multi_byte_varint():
    from utils.redpack_detector import _walk_pb
    # 89616640123043 from the live capture: 08 a3 c1 80 80 98 b0 14
    out = _walk_pb(bytes([0x08, 0xa3, 0xc1, 0x80, 0x80, 0x98, 0xb0, 0x14]))
    assert out == [{"field": 1, "wire": 0, "value": 89616640123043}]


def test_walker_decodes_length_delim():
    from utils.redpack_detector import _walk_pb
    # field 5, len 3, bytes "abc"
    out = _walk_pb(bytes([0x2a, 0x03, ord("a"), ord("b"), ord("c")]))
    assert out == [{"field": 5, "wire": 2, "bytes": b"abc"}]


def test_walker_handles_truncated_input_gracefully():
    from utils.redpack_detector import _walk_pb
    # Tag claims a 10-byte string but only 3 bytes follow → stop, no crash.
    out = _walk_pb(bytes([0x2a, 0x0a, 0x61, 0x62, 0x63]))
    # We bail before recording the truncated entry — no exception.
    assert out == []


# ──────────────────────────────────────────────────────────────────────
# RedBagEntry parsing — full sample from the live capture
# ──────────────────────────────────────────────────────────────────────


# One full entry's bytes, captured 2026-05-20 from emulator-5554:
# field 1 (bag_id) = 89616640123043
# field 2 (??)     = 110712
# field 3 (type)   = 2
# field 4 (sender_id) = 89565100511322
# field 5 (sender_name) = "全家禮服店"
# field 6 (timestamp) = 1779291863
_LIVE_ENTRY = bytes([
    0x08, 0xa3, 0xc1, 0x80, 0x80, 0x98, 0xb0, 0x14,
    0x10, 0xf8, 0xe0, 0x06,
    0x18, 0x02,
    0x20, 0xda, 0xa0, 0x80, 0x80, 0xd8, 0xae, 0x14,
    0x2a, 0x0f, 0xe5, 0x85, 0xa8, 0xe5, 0xae, 0xb6, 0xe7, 0xa6, 0xae, 0xe6, 0x9c, 0x8d, 0xe5, 0xba, 0x97,
    0x30, 0xd7, 0xad, 0xb7, 0xd0, 0x06,
])


def test_parse_redbag_entry_extracts_all_fields():
    from utils.redpack_detector import _parse_redbag_entry
    e = _parse_redbag_entry(_LIVE_ENTRY)
    assert e is not None
    assert e.bag_id == 89616640123043
    assert e.cfg_id == 110712       # proto field 2
    assert e.state == 2             # proto field 3 — UnClaimed
    # Back-compat aliases (still accessible for legacy callers).
    assert e.field_2 == 110712
    assert e.type_ == 2
    assert e.sender_id == 89565100511322
    assert e.sender_name == "全家禮服店"
    assert e.timestamp == 1779291863


def test_parse_redbag_entry_returns_none_when_missing_fields():
    from utils.redpack_detector import _parse_redbag_entry
    # Only field 1 — missing 4 and 5 required fields → None
    assert _parse_redbag_entry(bytes([0x08, 0x01])) is None


# ──────────────────────────────────────────────────────────────────────
# Full response parse — `repeated RedBagEntry list = 2;`
# ──────────────────────────────────────────────────────────────────────


def _wrap_repeated(*entries: bytes) -> bytes:
    """Wrap each entry as field 2 (length-delim) inside an outer container."""
    out = b""
    for e in entries:
        out += bytes([0x12]) + _varint_bytes(len(e)) + e
    return out


def _varint_bytes(v: int) -> bytes:
    out = b""
    while v > 0x7f:
        out += bytes([(v & 0x7f) | 0x80])
        v >>= 7
    out += bytes([v & 0x7f])
    return out


def test_parse_response_handles_empty():
    from utils.redpack_detector import _parse_redbag_list_response
    assert _parse_redbag_list_response(b"") == []


def test_parse_response_single_entry():
    from utils.redpack_detector import _parse_redbag_list_response
    resp = _wrap_repeated(_LIVE_ENTRY)
    out = _parse_redbag_list_response(resp)
    assert len(out) == 1
    assert out[0].sender_name == "全家禮服店"


def test_parse_response_multiple_entries():
    from utils.redpack_detector import _parse_redbag_list_response
    resp = _wrap_repeated(_LIVE_ENTRY, _LIVE_ENTRY, _LIVE_ENTRY)
    out = _parse_redbag_list_response(resp)
    assert len(out) == 3
    assert all(e.bag_id == 89616640123043 for e in out)


# ──────────────────────────────────────────────────────────────────────
# RedPoint gate
# ──────────────────────────────────────────────────────────────────────


def test_redpoint_returns_false_when_node_missing():
    from utils.redpack_detector import main_redpoint_has_unread
    page = MagicMock()
    page.evaluate.return_value = None
    assert main_redpoint_has_unread(page) is False


def test_redpoint_returns_false_when_all_children_inactive():
    from utils.redpack_detector import main_redpoint_has_unread
    page = MagicMock()
    page.evaluate.return_value = {
        "container_active": True,
        "any_child_active": False,
        "children": [{"name": "point", "active": False}, {"name": "point1", "active": False}],
    }
    assert main_redpoint_has_unread(page) is False


def test_redpoint_returns_true_when_any_child_active():
    from utils.redpack_detector import main_redpoint_has_unread
    page = MagicMock()
    page.evaluate.return_value = {
        "container_active": True,
        "any_child_active": True,
        "children": [{"name": "point", "active": True}, {"name": "point1", "active": False}],
    }
    assert main_redpoint_has_unread(page) is True


def test_redpoint_swallows_eval_errors():
    from utils.redpack_detector import main_redpoint_has_unread
    page = MagicMock()
    page.evaluate.side_effect = RuntimeError("page closed")
    assert main_redpoint_has_unread(page) is False


# ──────────────────────────────────────────────────────────────────────
# fetch_redbag_list wiring (install → send → drain → parse)
# ──────────────────────────────────────────────────────────────────────


def test_fetch_redbag_list_returns_empty_when_install_fails():
    from utils.redpack_detector import fetch_redbag_list
    page = MagicMock()
    page.evaluate.return_value = "no_cnet"
    assert fetch_redbag_list(page) == []


def test_fetch_redbag_list_returns_empty_when_send_fails():
    from utils.redpack_detector import fetch_redbag_list
    page = MagicMock()
    # First call: install ok. Second: clear ok. Third: send fails.
    page.evaluate.side_effect = ["installed", "cleared", {"err": "boom"}]
    assert fetch_redbag_list(page) == []


def test_fetch_redbag_list_returns_empty_on_timeout():
    from utils.redpack_detector import fetch_redbag_list
    page = MagicMock()
    # install → cleared → sent → drain returns [] forever
    page.evaluate.side_effect = ["installed", "cleared", {"sent": True}] + [[]] * 50
    with patch("utils.redpack_detector.time.sleep"), \
         patch("utils.redpack_detector.time.monotonic",
               side_effect=[0.0, 0.5, 1.0, 1.5, 2.1]):
        assert fetch_redbag_list(page, timeout=2.0) == []


def test_fetch_redbag_list_parses_response_body():
    from utils.redpack_detector import fetch_redbag_list
    page = MagicMock()
    resp_body = _wrap_repeated(_LIVE_ENTRY, _LIVE_ENTRY)
    page.evaluate.side_effect = [
        "installed",     # install
        "cleared",       # clear
        {"sent": True},  # send 0x2605
        [{"ts": 100, "body": list(resp_body)}],  # drain returns one frame
    ]
    with patch("utils.redpack_detector.time.sleep"):
        out = fetch_redbag_list(page, timeout=2.0)
    assert len(out) == 2
    assert out[0].sender_name == "全家禮服店"


def test_fetch_redbag_list_uses_latest_frame_when_multiple():
    """If somehow multiple 0x2605 RX frames are in the ring, use the newest."""
    from utils.redpack_detector import fetch_redbag_list
    page = MagicMock()
    fresh_body = _wrap_repeated(_LIVE_ENTRY)
    stale_body = b""  # empty list
    page.evaluate.side_effect = [
        "installed", "cleared", {"sent": True},
        [{"ts": 50, "body": list(stale_body)}, {"ts": 100, "body": list(fresh_body)}],
    ]
    with patch("utils.redpack_detector.time.sleep"):
        out = fetch_redbag_list(page, timeout=2.0)
    assert len(out) == 1


# ──────────────────────────────────────────────────────────────────────
# has_claimable_redpack — combined gate + fetch
# ──────────────────────────────────────────────────────────────────────


def test_has_claimable_always_fetches_now_that_gate_is_removed():
    """has_claimable_redpack no longer gates on RedPoint (that indicator
    only fires for unread chat messages, not server-side claimable bags).
    So fetch_redbag_list MUST be called, regardless of RedPoint state."""
    from utils.redpack_detector import has_claimable_redpack
    page = MagicMock()
    with patch("utils.redpack_detector.fetch_redbag_list", return_value=[]) as mock_fetch:
        assert has_claimable_redpack(page) is False
    mock_fetch.assert_called_once_with(page)


def test_has_claimable_returns_true_when_gate_and_fetch_both_positive():
    from utils.redpack_detector import has_claimable_redpack, RedBagEntry
    page = MagicMock()
    page.evaluate.return_value = {"any_child_active": True, "container_active": True, "children": []}
    fake_entry = RedBagEntry(bag_id=1, sender_id=2, sender_name="x", timestamp=3,
                             state=2, cfg_id=110712)
    with patch("utils.redpack_detector.fetch_redbag_list", return_value=[fake_entry]):
        assert has_claimable_redpack(page) is True


def test_has_claimable_returns_false_when_fetch_empty():
    """API returns no bags — has_claimable is False regardless of RedPoint."""
    from utils.redpack_detector import has_claimable_redpack
    page = MagicMock()
    with patch("utils.redpack_detector.fetch_redbag_list", return_value=[]):
        assert has_claimable_redpack(page) is False


# ──────────────────────────────────────────────────────────────────────
# claim_redpack — 0x2603 grab action
# ──────────────────────────────────────────────────────────────────────


def test_build_grab_body_matches_live_capture():
    """Body for bag_id=89616640123043, type=2 must encode to exactly
    `08 a3 c1 80 80 98 b0 14 10 02` (verified vs live server)."""
    from utils.redpack_detector import _build_grab_body
    expected = bytes([0x08, 0xa3, 0xc1, 0x80, 0x80, 0x98, 0xb0, 0x14, 0x10, 0x02])
    assert _build_grab_body(89616640123043, 2) == expected


def _mock_cfg_type_lookup(cfg_type: int = 1):
    """Helper: stub `_lookup_cfg_type` to return a fixed type."""
    return patch("utils.redpack_detector._lookup_cfg_type", return_value=cfg_type)


def test_claim_success_when_2603_response_received():
    """A 0x2603 RX with parseable body means server accepted the grab."""
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    success_body = bytes([0x08, 0x01, 0x10, 0x58])
    page.evaluate.side_effect = [
        "installed", "cleared", {"sent": True},
        [{"cmd": 0x2603, "ts": 100, "body": list(success_body)}],
    ]
    with patch("utils.redpack_detector.time.sleep"), _mock_cfg_type_lookup(1):
        r = claim_redpack(page, bag_id=42, cfg_id=110712)
    assert r.success is True
    assert r.response_cmd == 0x2603
    assert r.error is None
    assert r.response_fields == {1: 1, 2: 88}


def test_claim_failure_when_0201_error_received():
    """Server error response (0x0201) with error_code 2 = already claimed."""
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    err_body = bytes([0x08, 0x02])
    page.evaluate.side_effect = [
        "installed", "cleared", {"sent": True},
        [{"cmd": 0x0201, "ts": 100, "body": list(err_body)}],
    ]
    with patch("utils.redpack_detector.time.sleep"), _mock_cfg_type_lookup(1):
        r = claim_redpack(page, bag_id=42, cfg_id=110712)
    assert r.success is False
    assert r.response_cmd == 0x0201
    assert r.error_code == 2
    assert "code=2" in (r.error or "")


def test_claim_prefers_2603_over_0201_in_race():
    """If both fire (server quirk), prefer the success channel."""
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    page.evaluate.side_effect = [
        "installed", "cleared", {"sent": True},
        [
            {"cmd": 0x0201, "ts": 100, "body": [0x08, 0x02]},
            {"cmd": 0x2603, "ts": 101, "body": [0x08, 0x2a]},
        ],
    ]
    with patch("utils.redpack_detector.time.sleep"), _mock_cfg_type_lookup(1):
        r = claim_redpack(page, bag_id=42, cfg_id=110712)
    assert r.success is True
    assert r.response_cmd == 0x2603


def test_claim_timeout_when_no_response():
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    page.evaluate.side_effect = ["installed", "cleared", {"sent": True}] + [[]] * 50
    with patch("utils.redpack_detector.time.sleep"), \
         patch("utils.redpack_detector.time.monotonic", side_effect=[0.0, 0.3, 0.6, 0.9, 1.2, 1.5, 2.5]), \
         _mock_cfg_type_lookup(1):
        r = claim_redpack(page, bag_id=42, cfg_id=110712, timeout=2.0)
    assert r.success is False
    assert "timeout" in (r.error or "")


def test_claim_skipped_when_cfg_id_not_in_config():
    """If `configRed_packet.getDataByKey(cfg_id)` returns null, we can't
    derive the grab `type` — return error instead of sending a guess."""
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    with patch("utils.redpack_detector._lookup_cfg_type", return_value=None):
        r = claim_redpack(page, bag_id=42, cfg_id=999999)
    assert r.success is False
    assert "not found" in (r.error or "")


def test_claim_install_failure_returns_error():
    from utils.redpack_detector import claim_redpack
    page = MagicMock()
    page.evaluate.return_value = "no_cnet"
    with _mock_cfg_type_lookup(1):
        r = claim_redpack(page, bag_id=42, cfg_id=110712)
    assert r.success is False
    assert "install" in (r.error or "").lower()


def test_claim_all_pending_iterates_each_entry_and_counts_success():
    from utils.redpack_detector import claim_all_pending, RedBagEntry, ClaimResult
    page = MagicMock()
    entries = [
        RedBagEntry(bag_id=1, sender_id=10, sender_name="a", timestamp=0, state=2, cfg_id=110712),
        RedBagEntry(bag_id=2, sender_id=20, sender_name="b", timestamp=0, state=2, cfg_id=100410),
        RedBagEntry(bag_id=3, sender_id=30, sender_name="c", timestamp=0, state=2, cfg_id=120162),
    ]
    # 1st succeeds, 2nd fails, 3rd succeeds.
    results = [
        ClaimResult(bag_id=1, success=True),
        ClaimResult(bag_id=2, success=False, error="code=2", error_code=2),
        ClaimResult(bag_id=3, success=True),
    ]
    with patch("utils.redpack_detector.fetch_redbag_list", return_value=entries), \
         patch("utils.redpack_detector.claim_redpack", side_effect=results), \
         patch("utils.redpack_detector.time.sleep"):
        claimed, out = claim_all_pending(page)
    assert claimed == 2
    assert len(out) == 3
    assert [r.bag_id for r in out] == [1, 2, 3]

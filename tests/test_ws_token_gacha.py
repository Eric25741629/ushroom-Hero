"""Unit tests for ws_token.gacha (pure-WS 抽卡) — body/parse/ladder/drain logic.

No live socket: a scripted FakeClient mimics WSGameClient.call_for, returning a
0x0902 result with N drawn items when the (fixed-cost) bundle is affordable, else
the 0x0201 reject the real server sends on insufficient tickets.
"""
from __future__ import annotations

import config_manager
from ws_token import codec, gacha


# --- body builder + result parser -------------------------------------------

def test_build_draw_body_skill_15():
    assert gacha.build_draw_body(1, 15) == bytes([0x08, 0x01, 0x10, 0x0F])


def test_build_draw_body_companion_999():
    # 999 = varint e7 07; type 2
    assert gacha.build_draw_body(2, 999) == bytes([0x08, 0x02, 0x10, 0xE7, 0x07])


def _result_body(n_items: int) -> bytes:
    header = codec.pb_msg(1, codec.pb_uint(1, 1))
    items = b"".join(codec.pb_msg(2, codec.pb_uint(1, 10000 + i))
                     for i in range(n_items))
    return header + items


def test_parse_draw_result_counts_items():
    r = gacha.parse_draw_result(gacha.CMD_DRAW, _result_body(35))
    assert r.success and r.drawn == 35 and not r.rejected


def test_parse_draw_result_reject():
    body = codec.pb_uint(1, 37)  # error_code 37
    r = gacha.parse_draw_result(gacha.CMD_ERROR, body)
    assert not r.success and r.rejected and r.error_code == 37


def test_largest_affordable_ladder():
    assert gacha.largest_affordable(56588) == 999
    assert gacha.largest_affordable(800) == 999
    assert gacha.largest_affordable(799) == 35
    assert gacha.largest_affordable(30) == 35
    assert gacha.largest_affordable(29) == 15
    assert gacha.largest_affordable(15) == 15
    assert gacha.largest_affordable(14) is None


# --- scripted client ---------------------------------------------------------

class FakeClient:
    """Mimics WSGameClient.call_for for the draw cmd against a ticket budget."""

    def __init__(self, budget: int):
        self.budget = budget
        self.calls: list[tuple[int, bool]] = []   # (count, accepted)

    def call_for(self, cmd, body, *, expect_cmds, timeout=None):
        assert cmd == gacha.CMD_DRAW
        count = codec.walk_dict(body).get(2)
        cost = gacha.BUNDLE_COST.get(count, count)
        if self.budget >= cost:
            self.budget -= cost
            self.calls.append((count, True))
            return (gacha.CMD_DRAW, _result_body(count))
        self.calls.append((count, False))
        return (gacha.CMD_ERROR, codec.pb_uint(1, 37))


class FakeTracker:
    def __init__(self, counts: dict):
        self.counts = dict(counts)

    def has_item(self, item_id: int) -> bool:
        return int(item_id) in self.counts


def test_run_gacha_disabled_skips():
    rep = gacha.run_gacha(FakeClient(0), enabled=False, draw_type=1)
    assert rep.skipped and rep.total_drawn == 0


def test_run_gacha_bad_type():
    rep = gacha.run_gacha(FakeClient(1000), enabled=True, draw_type=9)
    assert rep.stopped_reason == "bad_type:9" and rep.total_drawn == 0


def test_run_gacha_drain_reject_driven_no_seed():
    """No tracker seed -> reject-driven ladder; drains budget below 15."""
    client = FakeClient(1700)
    rep = gacha.run_gacha(client, enabled=True, draw_type=1, mode="drain")
    assert rep.stopped_reason == "exhausted"
    assert client.budget < gacha.BUNDLE_COST[15]   # fully drained
    assert rep.total_drawn > 0 and rep.bundles > 0
    # blind path learns the boundary via 0x0201 rejects (≥1 step-down)
    assert any(not accepted for (_cnt, accepted) in client.calls)
    assert rep.bundles == sum(1 for (_c, ok) in client.calls if ok)


def test_run_gacha_drain_seeded_no_reject_calls():
    """Tracker seed (login snapshot) -> remaining-driven, no reject round-trips."""
    client = FakeClient(1700)
    tracker = FakeTracker({gacha.TICKET_ITEM[1]: 1700})
    rep = gacha.run_gacha(client, tracker, enabled=True, draw_type=1, mode="drain")
    assert rep.stopped_reason == "exhausted"
    assert client.budget < gacha.BUNDLE_COST[15]
    # seeded path never asks for a bundle it can't afford -> zero 0x0201 replies
    assert all(accepted for (_cnt, accepted) in client.calls)


def test_run_gacha_drain_both_paths_same_total():
    seeded = gacha.run_gacha(FakeClient(2400),
                             FakeTracker({gacha.TICKET_ITEM[1]: 2400}),
                             enabled=True, draw_type=1, mode="drain")
    blind = gacha.run_gacha(FakeClient(2400), enabled=True, draw_type=1,
                            mode="drain")
    assert seeded.total_drawn == blind.total_drawn


def test_run_gacha_fixed_count_batches():
    client = FakeClient(10_000)
    rep = gacha.run_gacha(client, enabled=True, draw_type=2, mode="fixed",
                          count=35, batches=3)
    assert rep.total_drawn == 105 and rep.bundles == 3
    assert rep.stopped_reason is None


def test_run_gacha_fixed_bad_count():
    rep = gacha.run_gacha(FakeClient(1000), enabled=True, draw_type=1,
                          mode="fixed", count=7, batches=1)
    assert rep.stopped_reason == "bad_count:7"


def test_run_gacha_fixed_stops_on_reject():
    client = FakeClient(45)  # affords one 35 (cost 30), then rejects
    rep = gacha.run_gacha(client, enabled=True, draw_type=1, mode="fixed",
                          count=35, batches=5)
    assert rep.bundles == 1 and rep.total_drawn == 35
    assert rep.stopped_reason and rep.stopped_reason.startswith("reject")


# --- config sanitizer --------------------------------------------------------

def test_config_default_has_gacha_block():
    ws = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]
    assert ws["gacha"]["enabled"] is False
    assert ws["gacha"]["types"] == [1, 2]


def test_merge_ws_token_sanitizes_gacha():
    default = config_manager.DEFAULT_DEVICE_CONFIG["ws_token"]["gacha"]
    merged = config_manager._merge_ws_token_phase_config(
        {"gacha": {"enabled": True, "types": [2, 9], "mode": "bogus",
                   "count": 7, "batches": 99999}})
    g = merged["gacha"]
    assert g["enabled"] is True
    assert g["types"] == [2]                  # 9 dropped
    assert g["mode"] == default["mode"]       # bogus -> default
    assert g["count"] == default["count"]     # 7 not a bundle -> default
    assert g["batches"] == 2000               # clamped to max


def test_merge_ws_token_gacha_defaults_when_absent():
    merged = config_manager._merge_ws_token_phase_config({})
    assert merged["gacha"]["enabled"] is False

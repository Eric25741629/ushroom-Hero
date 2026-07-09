"""Tests for ws_token.carpark_decoration — PURE cost-effectiveness picker.

The picker decides which 車友商行 decorations are most cost-effective to BUY +
UPGRADE under bounded attempts, never exceeding budget. Metric = cost per benefit
(currency spent per stat point); lowest first.

No WS / no I/O — every test builds a plain catalog + owned snapshot and asserts on
the returned DecorationPlan. AAA structure, behaviour-named.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token.carpark_decoration import (  # noqa: E402
    CatalogRow,
    pick_best_decoration,
)


def _row(id, level, cost, benefit, **kw):
    return CatalogRow(id=id, level=level, cost=cost, benefit=benefit, **kw)


# A catalog with three buyable decorations (level 1 == the unlock/buy row). Cost
# per benefit: A=100/10=10, B=60/10=6 (best), C=200/10=20 (worst).
BASIC_CATALOG = [
    _row(101, 1, cost=100, benefit=10),
    _row(102, 1, cost=60, benefit=10),
    _row(103, 1, cost=200, benefit=10),
]


def test_picks_lowest_cost_per_benefit_first():
    # Arrange: 3 decorations, B (102) is cheapest per stat point.
    # Act
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=10_000,
                                max_buys=1, max_upgrades=0)
    # Assert: only B chosen, as a buy.
    assert plan.buys == 1
    assert [s.id for s in plan.steps] == [102]
    assert plan.steps[0].kind == "buy"
    assert plan.total_cost == 60
    assert plan.total_benefit == 10


def test_orders_multiple_buys_by_cost_per_benefit():
    # Arrange: allow 2 buys with ample budget.
    # Act
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=10_000,
                                max_buys=2, max_upgrades=0)
    # Assert: B then A (6 then 10); C (20) excluded by max_buys.
    assert [s.id for s in plan.steps] == [102, 101]
    assert plan.buys == 2
    assert plan.total_cost == 160


def test_respects_max_buys_cap():
    # Arrange: only 1 buy allowed though budget covers all.
    # Act
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=10_000,
                                max_buys=1, max_upgrades=0)
    # Assert
    assert plan.buys == 1
    assert len(plan.steps) == 1


def test_respects_max_upgrades_cap_and_excludes_buys():
    # Arrange: a decoration already owned at level 1, with two upgrade rows.
    catalog = [
        _row(201, 1, cost=50, benefit=10),    # the buy row (already owned)
        _row(201, 2, cost=40, benefit=10),    # upgrade L1->L2  (ratio 4)
        _row(201, 3, cost=90, benefit=10),    # upgrade L2->L3  (ratio 9)
        _row(202, 1, cost=10, benefit=10),    # a cheap unowned buy (ratio 1)
    ]
    # Act: 1 upgrade allowed, 0 buys.
    plan = pick_best_decoration(catalog, owned={201: 1}, budget=10_000,
                                max_buys=0, max_upgrades=1)
    # Assert: the cheap buy (202) is NOT taken (max_buys=0); only the L1->L2
    # upgrade of the owned 201 is taken.
    assert plan.buys == 0
    assert plan.upgrades == 1
    assert plan.steps[0].id == 201
    assert plan.steps[0].kind == "upgrade"
    assert (plan.steps[0].from_level, plan.steps[0].to_level) == (1, 2)
    assert plan.total_cost == 40


def test_chained_upgrades_compete_on_marginal_cost():
    # Arrange: owned at L1; two further levels exist with rising marginal cost.
    catalog = [
        _row(301, 1, cost=10, benefit=10),
        _row(301, 2, cost=20, benefit=10),   # ratio 2
        _row(301, 3, cost=80, benefit=10),   # ratio 8
        _row(302, 1, cost=50, benefit=10),   # unowned buy, ratio 5
    ]
    # Act: allow 2 upgrades + 1 buy, ample budget.
    plan = pick_best_decoration(catalog, owned={301: 1}, budget=10_000,
                                max_buys=1, max_upgrades=2)
    # Assert: order = 301 L1->L2 (2), 302 buy (5), 301 L2->L3 (8).
    assert [(s.id, s.kind) for s in plan.steps] == [
        (301, "upgrade"), (302, "buy"), (301, "upgrade"),
    ]
    assert plan.upgrades == 2
    assert plan.buys == 1


def test_budget_is_never_exceeded():
    # Arrange: budget only covers the two cheapest buys (60 + 100 = 160).
    # Act
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=160,
                                max_buys=3, max_upgrades=0)
    # Assert: C (200) cannot be afforded after B+A.
    assert plan.total_cost <= 160
    assert {s.id for s in plan.steps} == {101, 102}


def test_budget_too_small_for_anything_returns_empty():
    # Arrange: budget below the cheapest step.
    # Act
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=10,
                                max_buys=3, max_upgrades=3)
    # Assert
    assert plan.steps == ()
    assert plan.total_cost == 0
    assert plan.skipped_reason == "no_budget"


def test_skips_already_maxed_decoration():
    # Arrange: decoration owned at its top level (no level 2 row exists).
    catalog = [
        _row(401, 1, cost=50, benefit=10),   # owned at L1, no L2 -> maxed
        _row(402, 1, cost=70, benefit=10),   # a buyable alternative
    ]
    # Act
    plan = pick_best_decoration(catalog, owned={401: 1}, budget=10_000,
                                max_buys=1, max_upgrades=5)
    # Assert: 401 has no next row -> only 402 (a buy) is planned.
    assert [s.id for s in plan.steps] == [402]
    assert plan.upgrades == 0
    assert plan.buys == 1


def test_empty_catalog_returns_empty_plan():
    # Act
    plan = pick_best_decoration([], owned={}, budget=10_000,
                                max_buys=5, max_upgrades=5)
    # Assert
    assert plan.steps == ()
    assert plan.total_cost == 0
    assert plan.total_benefit == 0
    assert plan.skipped_reason == "no_catalog"


def test_zero_quota_returns_empty_plan():
    # Act: both attempt caps are zero.
    plan = pick_best_decoration(BASIC_CATALOG, owned={}, budget=10_000,
                                max_buys=0, max_upgrades=0)
    # Assert
    assert plan.steps == ()
    assert plan.skipped_reason == "zero_quota"


def test_skips_zero_benefit_rows():
    # Arrange: a cosmetic-only decoration (benefit 0) plus a real one.
    catalog = [
        _row(501, 1, cost=10, benefit=0),    # pure cosmetic -> never picked
        _row(502, 1, cost=100, benefit=10),  # real stat decoration
    ]
    # Act
    plan = pick_best_decoration(catalog, owned={}, budget=10_000,
                                max_buys=5, max_upgrades=5)
    # Assert: only the stat decoration is in the plan.
    assert [s.id for s in plan.steps] == [502]


def test_accepts_catalog_native_dict_rows():
    # Arrange: rows in the configParking_design-native shape
    # {id, level, expend:[[goods,amt]], own_attrs:[[attr,val]]}.
    catalog = [
        {"id": 601, "level": 1, "expend": [[10001, 80]],
         "own_attrs": [[1, 10]]},
        {"id": 602, "level": 1, "expend": [[10001, 50]],
         "own_attrs": [[1, 10]]},
    ]
    # Act
    plan = pick_best_decoration(catalog, owned={}, budget=10_000,
                                max_buys=1, max_upgrades=0)
    # Assert: 602 is cheaper per benefit (5 vs 8); currency goods parsed.
    assert plan.steps[0].id == 602
    assert plan.steps[0].cost == 50


def test_accepts_skin_list_owned_shape():
    # Arrange: owned given as skin_list-like entries {skin_id, skin_lev}.
    catalog = [
        _row(701, 1, cost=50, benefit=10),
        _row(701, 2, cost=30, benefit=10),  # upgrade available
    ]
    owned = [{"skin_id": 701, "skin_lev": 1, "k": 2, "v": 1467}]
    # Act
    plan = pick_best_decoration(catalog, owned=owned, budget=10_000,
                                max_buys=0, max_upgrades=1)
    # Assert: recognised as owned L1 -> only the L1->L2 upgrade planned.
    assert plan.upgrades == 1
    assert plan.steps[0].to_level == 2


# ───────────────────────────────────────────────────────────────────────────
# plan_upgrades — price/limit-aware planner for the dashboard one-click tool.
# ───────────────────────────────────────────────────────────────────────────

from ws_token.carpark_decoration import (  # noqa: E402
    DecoUpgradeState,
    plan_upgrades,
)


def _deco(id, name, price, limit, steps):
    return DecoUpgradeState(id=id, name=name, price_per_frag=price,
                            limit_remaining=limit, steps=tuple(steps))


def test_plan_picks_lowest_coin_per_attr_first():
    # Arrange: A frag cheaper (100) than B (300); same attr/frags per star.
    decos = [
        _deco(1, "A", price=100, limit=999, steps=[(7, 4, 48000)]),
        _deco(2, "B", price=300, limit=999, steps=[(7, 4, 48000)]),
    ]
    # Act
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=1)
    # Assert: A is cheaper per attr (400 vs 1200) -> chosen.
    assert [s.id for s in plan.steps] == [1]
    assert plan.steps[0].coin == 400
    assert plan.total_attr == 48000


def test_plan_interleaves_by_coin_per_attr_across_decos():
    # A: 1st star 4 frags@100=400, 2nd star 6 frags@100=600.
    # B: 1st star 10 frags@50=500.  Order by coin: A1(400) < B1(500) < A2(600).
    decos = [
        _deco(1, "A", price=100, limit=999, steps=[(7, 4, 48000), (8, 6, 48000)]),
        _deco(2, "B", price=50, limit=999, steps=[(11, 10, 48000)]),
    ]
    # Act
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=5)
    # Assert: interleaved A,B,A by ascending coin-per-attr.
    assert [(s.id, s.coin) for s in plan.steps] == [(1, 400), (2, 500), (1, 600)]
    assert plan.total_coin == 1500


def test_plan_respects_budget():
    decos = [
        _deco(1, "A", price=100, limit=999, steps=[(7, 4, 48000), (8, 6, 48000)]),
    ]
    # Act: only 500 coin -> first star (400) fits, second (600) does not.
    plan = plan_upgrades(decos, budget=500, max_steps=9)
    # Assert
    assert [s.coin for s in plan.steps] == [400]
    assert plan.total_coin == 400


def test_plan_respects_limit_remaining():
    # Only 5 fragments may still be bought for this deco; star1=4 fits, star2=6
    # would need 6 (only 1 left) -> stop.
    decos = [
        _deco(1, "A", price=100, limit=5, steps=[(7, 4, 48000), (8, 6, 48000)]),
    ]
    # Act
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=9)
    # Assert
    assert [s.to_level for s in plan.steps] == [7]
    assert plan.total_frags == 4


def test_plan_respects_max_steps():
    decos = [
        _deco(1, "A", price=100, limit=999,
              steps=[(7, 4, 48000), (8, 4, 48000), (9, 4, 48000)]),
    ]
    # Act
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=2)
    # Assert
    assert len(plan.steps) == 2
    assert [s.to_level for s in plan.steps] == [7, 8]


def test_plan_skips_zero_attr_gain():
    decos = [_deco(1, "A", price=100, limit=999, steps=[(7, 4, 0)])]
    # Act
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=9)
    # Assert: cosmetic star never worth coin.
    assert plan.steps == ()
    assert plan.skipped_reason == "no_candidate"


def test_plan_empty_decos_reason():
    assert plan_upgrades([], budget=10_000, max_steps=5).skipped_reason == "no_decos"


def test_plan_zero_budget_reason():
    decos = [_deco(1, "A", price=100, limit=999, steps=[(7, 4, 48000)])]
    assert plan_upgrades(decos, budget=0, max_steps=5).skipped_reason == "no_budget"


def test_plan_from_level_tracks_chain():
    # A deco starting at level 6 (first step to 7) reports correct from/to levels.
    decos = [_deco(1, "A", price=100, limit=999,
                   steps=[(7, 4, 48000), (8, 5, 48000)])]
    plan = plan_upgrades(decos, budget=10_000_000, max_steps=2)
    assert [(s.from_level, s.to_level) for s in plan.steps] == [(6, 7), (7, 8)]


# ───────────────────────────────────────────────────────────────────────────
# Execution batching — the dashboard executor groups the (cost-optimal) planned
# steps by decoration and buys each decoration's whole frag batch in ONE 6914,
# then upgrades its stars consecutively. Stubs the WS layer to assert grouping.
# ───────────────────────────────────────────────────────────────────────────


def test_execute_batches_frag_buys_per_decoration(monkeypatch):
    from control_panel import routes_carpark_decorate_tools as R

    # Two decorations whose cost-optimal steps interleave (A1 400 < B1 500 < A2 600).
    state = {"coin": 10_000_000, "decos": [
        {"id": 1, "name": "A", "level": 6, "price": 100, "limit_remaining": 999,
         "shop_id": 11, "steps": [[7, 4, 48000], [8, 6, 48000]]},
        {"id": 2, "name": "B", "level": 10, "price": 50, "limit_remaining": 999,
         "shop_id": 22, "steps": [[11, 10, 48000]]},
    ]}
    monkeypatch.setattr(R, "_read_state", lambda ip: (state, None))
    monkeypatch.setattr(R, "_ws_client", lambda ip: ("client", None))
    monkeypatch.setattr(R, "_job_log", lambda jid, m: None)

    calls = []

    def fake_exec(client, *, shop_id, skin_id, frags, do_upgrade=True, **kw):
        calls.append((skin_id, frags, do_upgrade))
        # Buy-only reports the whole batch bought; upgrades buy nothing (held).
        fb = frags if do_upgrade is False else 0
        return ({"ok": True, "frags_bought": fb, "bought": True,
                 "after_level": kw.get("target_level")}, None)

    monkeypatch.setattr(R.deco_ws, "exec_buy_and_upgrade", fake_exec)

    captured = {}
    monkeypatch.setattr(R, "_job_update",
                        lambda jid, **kw: captured.update(kw.get("result") or {}))

    R._run_execute_job("testjid", "ip", 0, 30)

    # One batched buy-only per decoration, with SUMMED frags (A: 4+6=10, B: 10).
    buys = [(sid, fr) for (sid, fr, up) in calls if up is False]
    assert buys == [(1, 10), (2, 10)]
    # Upgrades run consecutively per decoration and buy nothing.
    ups = [sid for (sid, fr, up) in calls if up is True]
    assert ups == [1, 1, 2]
    # Coin accounted from batched buys only (100×10 + 50×10 = 1500); no reason.
    assert captured["spent"] == 1500
    assert captured["stopped_reason"] is None
    assert [e["ok"] for e in captured["executed"]] == [True, True, True]

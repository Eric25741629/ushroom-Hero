"""車友商行 decoration (裝飾) cost-effectiveness picker — PURE, no WS, no I/O.

The 車友商行 / 裝飾 shop lets you BUY a decoration (skin) and then UPGRADE its
level; each (id, level) row costs ``expend`` of a currency and grants a stat
``benefit`` (``own_attrs``). Both buy and upgrade go through the SAME server cmd
``car_park.car_park_skin_up_c2s`` (12817 {type, skin_id}); the server reads the
expend/own_attrs from the ``configParking_design`` table keyed by (id, level).
See docs/protocol/CARPARK_DECORATION_SHOP.md for the live-recon recipe and the
catalog source — shop_type is NOT a generic mall shop; this is its own family.

This module is the OFFLINE brain: given a catalog snapshot + what I already own +
a budget, it decides which decorations are the most cost-effective to buy and
upgrade, under a BOUNDED number of attempts (user requirement: "可嘗試購買與升級,
不能過多次"). It is intentionally I/O-free and WS-free so it can be unit-tested in
isolation; the live action layer (deferred until 10:00-22:00 recon) feeds it the
catalog and applies the returned plan.

Cost-effectiveness metric
--------------------------
We rank every candidate *step* (a buy of a not-owned decoration, or a +1 upgrade
of an owned one) by **cost per benefit** = ``cost / benefit`` — the currency
spent per stat point gained. Lowest ratio first: that is the cheapest place to
buy a unit of stat, which is exactly "most cost-effective". A step with
``benefit <= 0`` is never worth buying for stat value, so it is skipped (it would
divide by zero / be pure cosmetic). Steps are then committed greedily in
ascending ratio while the bounded-attempt and budget guardrails allow it.

Why greedy-by-ratio (not full knapsack): with a hard *attempt* cap (max_buys /
max_upgrades) the dominant constraint is the attempt count, not a tight budget,
so the cheapest-per-stat-first order maximises stat-per-attempt and stat-per-coin
together. It is O(n log n), deterministic, and trivially explainable in a log —
preferable to an opaque DP for a bounded, safety-critical purchase loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# A buy is modelled as upgrading from level 0 -> the catalog's base level. The
# catalog rows are keyed (id, level); a not-owned decoration is owned-level 0.
BASE_OWNED_LEVEL = 0


@dataclass(frozen=True)
class CatalogRow:
    """One (id, level) decoration row distilled from configParking_design.

    ``cost`` is ``expend[0][1]`` (amount of currency ``cost_goods``); ``benefit``
    is ``own_attrs[0][1]`` (the stat value this level grants). ``level`` is the
    decoration's own level this row represents (0 == the buy/unlock row).
    """

    id: int
    level: int
    cost: int
    benefit: int
    cost_goods: int = 0          # expend[0][0] — the currency goods id (info only)
    max_level: int | None = None  # highest level that exists for this id (info only)


@dataclass(frozen=True)
class Step:
    """One committed buy/upgrade in the plan."""

    id: int
    kind: str            # "buy" | "upgrade"
    from_level: int
    to_level: int
    cost: int
    benefit: int         # marginal stat gained by this single step

    @property
    def cost_per_benefit(self) -> float:
        return self.cost / self.benefit if self.benefit > 0 else float("inf")


@dataclass(frozen=True)
class DecorationPlan:
    """The bounded, budget-respecting set of steps to execute."""

    steps: tuple[Step, ...] = ()
    total_cost: int = 0
    total_benefit: int = 0
    buys: int = 0
    upgrades: int = 0
    skipped_reason: str | None = None  # set only when nothing was planned

    @property
    def cost_per_benefit(self) -> float:
        return (self.total_cost / self.total_benefit
                if self.total_benefit > 0 else float("inf"))


def _index_catalog(catalog) -> dict[tuple[int, int], CatalogRow]:
    """{(id, level): row}. Tolerates dict rows or CatalogRow instances."""
    out: dict[tuple[int, int], CatalogRow] = {}
    for raw in catalog or ():
        row = raw if isinstance(raw, CatalogRow) else _coerce_row(raw)
        if row is None:
            continue
        out[(row.id, row.level)] = row
    return out


def _coerce_row(raw: dict) -> CatalogRow | None:
    """Map a dict (e.g. straight from a config dump) into a CatalogRow.

    Accepts either flat {id, level, cost, benefit} or the catalog-native
    {id, level, expend:[[goods,amt]], own_attrs:[[attr,val]]} shape.
    """
    try:
        rid = int(raw["id"])
        level = int(raw.get("level", BASE_OWNED_LEVEL))
    except (KeyError, TypeError, ValueError):
        return None

    cost = raw.get("cost")
    cost_goods = raw.get("cost_goods", 0)
    if cost is None:
        expend = raw.get("expend") or ()
        if expend and isinstance(expend[0], (list, tuple)) and len(expend[0]) >= 2:
            cost_goods, cost = int(expend[0][0]), int(expend[0][1])
        else:
            cost = 0

    benefit = raw.get("benefit")
    if benefit is None:
        own = raw.get("own_attrs") or ()
        if own and isinstance(own[0], (list, tuple)) and len(own[0]) >= 2:
            benefit = int(own[0][1])
        else:
            benefit = 0

    return CatalogRow(id=rid, level=level, cost=int(cost), benefit=int(benefit),
                      cost_goods=int(cost_goods or 0),
                      max_level=raw.get("max_level"))


def _owned_levels(owned) -> dict[int, int]:
    """{decoration_id: owned_level}. ``owned`` may be {id: level} or an iterable
    of {skin_id/id, skin_lev/level} entries (mirrors car_park skin_list)."""
    out: dict[int, int] = {}
    if isinstance(owned, dict):
        for k, v in owned.items():
            try:
                out[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
        return out
    for e in owned or ():
        if isinstance(e, dict):
            rid = e.get("skin_id", e.get("id"))
            lev = e.get("skin_lev", e.get("level"))
        else:
            rid, lev = getattr(e, "id", None), getattr(e, "level", None)
        try:
            out[int(rid)] = int(lev)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


def _next_step(rid: int, owned_level: int,
               index: dict[tuple[int, int], CatalogRow],
               *, cumulative: bool = False) -> Step | None:
    """The single next buy/upgrade available for decoration ``rid`` from its
    current ``owned_level``, or None when the next row is absent (maxed out).

    ``benefit`` is the MARGINAL stat gained by this one step (使用者 2026-06-14:
    用「單次升級提供的屬性加成」判 CP 值). When ``cumulative`` is True the catalog's
    ``own_attrs`` is a running total, so this step's marginal gain is
    ``benefit(target) - benefit(from)``; otherwise each row already stores the
    per-level increment and is used as-is.
    """
    target = owned_level + 1 if owned_level >= 1 else _first_buyable_level(rid, index)
    if target is None:
        return None
    row = index.get((rid, target))
    if row is None:
        return None
    benefit = row.benefit
    if cumulative and owned_level >= 1:
        from_row = index.get((rid, owned_level))
        if from_row is not None:
            benefit = max(0, row.benefit - from_row.benefit)
    if row.cost <= 0 and benefit <= 0:
        return None  # free initial decoration / nothing to spend or gain
    kind = "upgrade" if owned_level >= 1 else "buy"
    return Step(id=rid, kind=kind, from_level=owned_level, to_level=target,
                cost=row.cost, benefit=benefit)


def _first_buyable_level(rid: int,
                         index: dict[tuple[int, int], CatalogRow]) -> int | None:
    """For a not-owned decoration, the lowest catalog level that costs something
    (the unlock/buy row). Levels are usually 1-based with 0 == free default."""
    levels = sorted(lv for (i, lv) in index if i == rid)
    for lv in levels:
        if lv <= BASE_OWNED_LEVEL:
            continue
        return lv
    return None


def pick_best_decoration(catalog, owned, budget, *, max_buys, max_upgrades,
                         cumulative_benefit=False):
    """Pick the most cost-effective bounded set of decoration buys/upgrades.

    Args:
        catalog: iterable of CatalogRow or dict rows (one per (id, level)). dict
            rows accept either {id, level, cost, benefit} or the catalog-native
            {id, level, expend, own_attrs} shape.
        owned: {id: owned_level} or an iterable of skin_list-like entries.
        budget: max total currency to spend (steps never exceed it cumulatively).
        max_buys: max number of NEW decorations to buy (bounded attempts).
        max_upgrades: max number of +1 upgrades of owned decorations (bounded).
        cumulative_benefit: set True if the catalog's own_attrs is a running TOTAL
            per level — then each step's CP value uses the marginal gain
            benefit(target)-benefit(from) (使用者 2026-06-14: 用單次升級的屬性加成判
            CP 值). False (default) = own_attrs is already the per-level increment.

    Returns:
        DecorationPlan. Greedy by ascending cost-per-benefit. Respects budget and
        both attempt caps; skips decorations already at max level (no next row)
        and zero/negative-benefit rows. ``skipped_reason`` is set when the plan is
        empty so the caller can log *why* (no_catalog / no_candidate / no_budget /
        zero_quota).
    """
    budget = max(0, _to_int(budget))
    max_buys = max(0, _to_int(max_buys))
    max_upgrades = max(0, _to_int(max_upgrades))

    if not catalog:
        return DecorationPlan(skipped_reason="no_catalog")
    if max_buys == 0 and max_upgrades == 0:
        return DecorationPlan(skipped_reason="zero_quota")

    index = _index_catalog(catalog)
    owned_level = _owned_levels(owned)
    # Seed the candidate frontier: one next step per decoration id present in the
    # catalog (owned ones upgrade from their level; not-owned ones buy the base).
    ids = sorted({i for (i, _lv) in index})
    frontier: dict[int, Step] = {}
    for rid in ids:
        step = _next_step(rid, owned_level.get(rid, 0), index,
                          cumulative=cumulative_benefit)
        if step is not None and step.benefit > 0:
            frontier[rid] = step

    if not frontier:
        return DecorationPlan(skipped_reason="no_candidate")

    chosen: list[Step] = []
    spent = 0
    buys = 0
    upgrades = 0

    # Greedy: repeatedly take the globally cheapest-per-benefit affordable step
    # whose attempt cap is not yet exhausted; after taking an upgrade, advance
    # that decoration's frontier to its next level so chained upgrades compete on
    # their true (rising) marginal cost.
    while frontier:
        best_rid = _pick_affordable(frontier, budget - spent,
                                    buys, upgrades, max_buys, max_upgrades)
        if best_rid is None:
            break
        step = frontier.pop(best_rid)
        chosen.append(step)
        spent += step.cost
        if step.kind == "buy":
            buys += 1
        else:
            upgrades += 1
        nxt = _next_step(step.id, step.to_level, index,
                         cumulative=cumulative_benefit)
        if nxt is not None and nxt.benefit > 0:
            frontier[step.id] = nxt

    if not chosen:
        reason = "no_budget" if budget >= 0 else "no_candidate"
        return DecorationPlan(skipped_reason=reason)

    return DecorationPlan(
        steps=tuple(chosen),
        total_cost=spent,
        total_benefit=sum(s.benefit for s in chosen),
        buys=buys,
        upgrades=upgrades,
    )


def _pick_affordable(frontier: dict[int, Step], remaining_budget: int,
                     buys: int, upgrades: int,
                     max_buys: int, max_upgrades: int) -> int | None:
    """Best (lowest cost-per-benefit) step id that fits budget + its attempt cap.

    Tie-break by lower absolute cost then lower id for determinism.
    """
    best_id: int | None = None
    best_key: tuple[float, int, int] | None = None
    for rid, step in frontier.items():
        if step.cost > remaining_budget:
            continue
        if step.kind == "buy" and buys >= max_buys:
            continue
        if step.kind == "upgrade" and upgrades >= max_upgrades:
            continue
        key = (step.cost_per_benefit, step.cost, rid)
        if best_key is None or key < best_key:
            best_key, best_id = key, rid
    return best_id


def _to_int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


# ───────────────────────────────────────────────────────────────────────────
# Price/limit-aware upgrade planner (dashboard "一鍵最佳升級車位裝飾").
#
# Models the REAL live economy captured 2026-06-15 (see
# docs/protocol/CARPARK_DECORATION_SHOP.md §9):
#   - Each decoration is upgraded one STAR at a time; reaching star m needs
#     ``frags`` fragments of THAT decoration's own item.
#   - Fragments are bought from the Mall with 菇車幣 at a PER-DECORATION price
#     (``price_per_frag`` differs 100k–600k between decorations).
#   - Per decoration there is a remaining purchase quota ``limit_remaining``
#     (限購 X/120 = how many MORE fragments can still be bought).
#   - The cost-effectiveness metric is coin-per-attr =
#     (frags × price_per_frag) / marginal_attr — lowest first (使用者 2026-06-15:
#     目標＝最大化屬性). Greedy by that ratio, bounded by a coin budget, a
#     per-decoration fragment quota, and a total max-steps cap.
# This is the OFFLINE brain; the dashboard read layer supplies the live numbers
# and the executor applies the returned steps via the cocos buy/upgrade UI.
# ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecoUpgradeState:
    """One owned decoration's plannable state.

    ``steps`` is the ordered ladder of remaining single-star upgrades, each a
    ``(to_level, frags, attr_gain)`` tuple from the current level upward (the
    read layer builds it from configParking_design: frags = expend of the
    departing level, attr_gain = own_attrs delta of that star).
    """

    id: int
    name: str
    price_per_frag: int
    limit_remaining: int
    steps: tuple[tuple[int, int, int], ...]
    # 背包已持有、可折抵購買的碎片數(0x0401 實讀)。沿階梯由低星往高星消耗;
    # 每步實際要買 = max(0, 該步碎片 − 剩餘持有),coin 只算「要買」的部分,
    # 免費(持有足)的星會排在最前(coin_per_attr=0)。預設 0 = 舊行為(全買)。
    held_frags: int = 0


@dataclass(frozen=True)
class UpgradeStep:
    """One committed +1 star upgrade in the plan."""

    id: int
    name: str
    from_level: int
    to_level: int
    frags: int              # 該星總需碎片(執行端用來自我修復買量 / 判 target)
    coin: int
    attr_gain: int
    buy_frags: int = 0      # 折抵持有後「實際要買」的碎片(顯示 / 花費用);frags − 持有

    @property
    def coin_per_attr(self) -> float:
        return self.coin / self.attr_gain if self.attr_gain > 0 else float("inf")


@dataclass(frozen=True)
class UpgradePlan:
    """The bounded, budget-respecting ordered set of star upgrades."""

    steps: tuple[UpgradeStep, ...] = ()
    total_coin: int = 0
    total_attr: int = 0
    total_frags: int = 0
    skipped_reason: str | None = None


def _coin_per_attr(coin: int, attr: int) -> float:
    return coin / attr if attr > 0 else float("inf")


def plan_upgrades(decos, *, budget, max_steps):
    """Greedy price/limit-aware plan of the most attr-efficient star upgrades.

    Args:
        decos: iterable of DecoUpgradeState (one per owned decoration). Each
            carries its per-fragment coin price, remaining purchase quota, and
            the ordered ladder of its remaining single-star upgrades.
        budget: max total 菇車幣 to spend (cumulative coin never exceeds it).
        max_steps: max number of single-star upgrades to commit (bound on how
            many actions the executor will perform per run).

    Returns:
        UpgradePlan. Steps are ordered by commit order (globally cheapest
        coin-per-attr first), each respecting the coin budget, the owning
        decoration's remaining fragment quota, and the total max-steps cap.
        Zero/negative attr_gain steps are skipped. ``skipped_reason`` is set
        when the plan is empty (no_decos / zero_quota / no_budget / no_candidate).
    """
    budget = max(0, _to_int(budget))
    max_steps = max(0, _to_int(max_steps))

    decos = [d for d in (decos or ()) if isinstance(d, DecoUpgradeState)]
    if not decos:
        return UpgradePlan(skipped_reason="no_decos")
    if max_steps == 0:
        return UpgradePlan(skipped_reason="zero_quota")
    if budget == 0:
        return UpgradePlan(skipped_reason="no_budget")

    # Mutable per-decoration cursor into its step ladder + remaining quota.
    state = {
        d.id: {"deco": d, "idx": 0, "level": _start_level(d),
               "frags_left": max(0, _to_int(d.limit_remaining)),
               "held_left": max(0, _to_int(d.held_frags))}
        for d in decos
    }

    chosen: list[UpgradeStep] = []
    spent = 0
    while len(chosen) < max_steps:
        best = _best_affordable_step(state, budget - spent)
        if best is None:
            break
        chosen.append(best)
        spent += best.coin
        st = state[best.id]
        # 持有碎片先折抵這一星,只有實買量(best.buy_frags)計入限購 quota。
        st["idx"] += 1
        st["level"] = best.to_level
        st["held_left"] = max(0, st["held_left"] - best.frags)
        st["frags_left"] -= best.buy_frags

    if not chosen:
        return UpgradePlan(skipped_reason="no_candidate")
    return UpgradePlan(
        steps=tuple(chosen),
        total_coin=spent,
        total_attr=sum(s.attr_gain for s in chosen),
        total_frags=sum(s.frags for s in chosen),
    )


def _start_level(d: DecoUpgradeState) -> int:
    """Level the decoration sits at before its first remaining step."""
    if d.steps:
        first_to = _to_int(d.steps[0][0])
        return first_to - 1
    return 0


def _candidate_step(st: dict, remaining_budget: int) -> UpgradeStep | None:
    """The next affordable+in-quota upgrade for one decoration, or None."""
    deco: DecoUpgradeState = st["deco"]
    idx = st["idx"]
    if idx >= len(deco.steps):
        return None
    to_level, frags, attr_gain = (
        _to_int(deco.steps[idx][0]), _to_int(deco.steps[idx][1]),
        _to_int(deco.steps[idx][2]))
    if attr_gain <= 0:
        return None  # cosmetic / no stat — never worth coin
    # 背包持有碎片折抵:實際要買的才花菇車幣、才占限購 quota。
    buy = max(0, frags - max(0, _to_int(st.get("held_left", 0))))
    coin = buy * max(0, _to_int(deco.price_per_frag))
    if coin > remaining_budget:
        return None
    if buy > st["frags_left"]:
        return None  # would exceed 限購 remaining quota
    return UpgradeStep(id=deco.id, name=deco.name, from_level=st["level"],
                       to_level=to_level, frags=frags, coin=coin,
                       attr_gain=attr_gain, buy_frags=buy)


def _best_affordable_step(state: dict, remaining_budget: int) -> UpgradeStep | None:
    """Lowest coin-per-attr step across all decorations; tie -> lower coin, id."""
    best: UpgradeStep | None = None
    best_key: tuple[float, int, int] | None = None
    for rid, st in state.items():
        step = _candidate_step(st, remaining_budget)
        if step is None:
            continue
        key = (step.coin_per_attr, step.coin, rid)
        if best_key is None or key < best_key:
            best, best_key = step, key
    return best

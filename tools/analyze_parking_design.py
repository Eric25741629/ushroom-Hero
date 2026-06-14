"""Analyze the dumped configParking_design catalog (curve signatures + cost model).

Reads docs/protocol/PARKING_DESIGN_CATALOG.json (from dump_parking_design.py).
Answers: are all decorations the same stat curve? does any use a shared currency
instead of its own fragment? what is the per-star marginal attr / power / cost?
"""
from __future__ import annotations

import io
import json
import sys
from collections import defaultdict

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

CATALOG = "docs/protocol/PARKING_DESIGN_CATALOG.json"
POSITION_NAME = {1: "門", 2: "地板", 3: "圍欄", 4: "路燈", 5: "擺件"}


def attr_sum(own_attrs):
    return sum(int(v) for (_a, v) in (own_attrs or []) if v is not None)


def main() -> None:
    with open(CATALOG, encoding="utf-8") as f:
        cat = json.load(f)
    rows = cat["rows"]
    attr_names = cat.get("attr_names", {})
    goods_names = cat.get("goods_names", {})

    by_id: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("id") is not None:
            by_id[int(r["id"])].append(r)
    for rid in by_id:
        by_id[rid].sort(key=lambda r: int(r["level"]))

    # 1) Curve signature per decoration: tuple of (level, attr_sum, power) +
    #    fragment-amount ladder. Group ids that share an identical signature.
    sig_groups: dict[tuple, list[int]] = defaultdict(list)
    for rid, rs in by_id.items():
        if rs[0].get("if_initial") == 1:
            continue
        attr_sig = tuple((int(r["level"]), attr_sum(r.get("own_attrs")),
                          int(r.get("power") or 0)) for r in rs)
        cost_sig = tuple(
            (int(r["level"]),
             int((r.get("expend") or [[0, 0]])[0][1]) if r.get("expend") else 0)
            for r in rs)
        sig_groups[(attr_sig, cost_sig)].append(rid)

    print(f"distinct upgradeable curve signatures: {len(sig_groups)}")
    for i, ((attr_sig, _cost_sig), ids) in enumerate(
            sorted(sig_groups.items(), key=lambda kv: -len(kv[1])), 1):
        maxlv = attr_sig[-1][0]
        max_attr = attr_sig[-1][1]
        max_power = attr_sig[-1][2]
        names = [by_id[i0][0].get("name") for i0 in ids[:4]]
        print(f"  sig#{i}: {len(ids)} decorations, max_lv={maxlv}, "
              f"max_attr_total={max_attr}, max_power={max_power} | "
              f"e.g. {names}")

    # 2) Cost model: does any decoration's expend use a goods id that is NOT its
    #    own fragment? Heuristic: self fragment is the goods whose name == deco name.
    print("\n--- expend currency check ---")
    shared_currency_ids = []
    for rid, rs in by_id.items():
        deco_name = rs[0].get("name")
        goods_used = set()
        for r in rs:
            for e in (r.get("expend") or []):
                goods_used.add(e[0])
        for g in goods_used:
            gname = goods_names.get(str(g))
            if gname and gname != deco_name:
                shared_currency_ids.append((rid, deco_name, g, gname))
    if shared_currency_ids:
        print("  decorations using a goods whose name != deco name:")
        for rid, dn, g, gn in shared_currency_ids:
            print(f"    id={rid} {dn!r} uses goods {g} ({gn})")
    else:
        print("  ALL decorations consume their OWN same-named fragment item.")

    # 3) Representative full ladder for the dominant signature (the standard one).
    std_ids = max(sig_groups.values(), key=len)
    rid = sorted(std_ids)[0]
    rs = by_id[rid]
    print(f"\n--- STANDARD ladder (representative id={rid} "
          f"{rs[0].get('name')!r}, applies to {len(std_ids)} decorations) ---")
    print(f"{'star':>4} {'frag_cost':>9} {'cum_frag':>9} "
          f"{'attr_total':>10} {'Δattr':>8} {'power':>8} {'Δpower':>8}")
    cum_frag = 0
    prev_attr = 0
    prev_pow = 0
    for r in rs:
        lv = int(r["level"])
        # expend on row N = cost to go N -> N+1; show as the cost to REACH N+1.
        cost_to_next = int((r.get("expend") or [[0, 0]])[0][1]) if r.get("expend") else 0
        a = attr_sum(r.get("own_attrs"))
        p = int(r.get("power") or 0)
        # frag spent to reach THIS star = expend of previous row
        if lv >= 1:
            prev_row = rs[lv - 1] if lv - 1 < len(rs) else None
            reach_cost = int((prev_row.get("expend") or [[0, 0]])[0][1]) if prev_row and prev_row.get("expend") else 0
            cum_frag += reach_cost
        print(f"{lv:>4} {cost_to_next:>9} {cum_frag:>9} {a:>10} "
              f"{a - prev_attr:>8} {p:>8} {p - prev_pow:>8}")
        prev_attr, prev_pow = a, p

    # 4) Which goods ids are the fragments + their names (for inventory lookup).
    print("\n--- fragment goods per category ---")
    for pos in sorted(POSITION_NAME):
        frags = []
        for rid2, rs2 in sorted(by_id.items()):
            if rs2[0].get("position") != pos or rs2[0].get("if_initial") == 1:
                continue
            for r in rs2:
                for e in (r.get("expend") or []):
                    frags.append((e[0], goods_names.get(str(e[0]), "?")))
                    break
                break
        seen = {}
        for g, n in frags:
            seen[g] = n
        print(f"  {POSITION_NAME[pos]}: " +
              ", ".join(f"{g}={n}" for g, n in sorted(seen.items())))


if __name__ == "__main__":
    main()

"""Extract per-decoration special skin effects (裝扮加成) + check if they scale."""
from __future__ import annotations
import io, json, sys
from collections import defaultdict
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

with open("docs/protocol/PARKING_DESIGN_CATALOG.json", encoding="utf-8") as f:
    cat=json.load(f)
rows=cat["rows"]
by_id=defaultdict(list)
for r in rows:
    if r.get("id") is not None: by_id[int(r["id"])].append(r)
for rid in by_id: by_id[rid].sort(key=lambda r:int(r["level"]))

print("=== per-decoration desc + effect (level 1 vs max) ===")
for rid, rs in sorted(by_id.items()):
    if rs[0].get("if_initial")==1: continue
    lv1=next((r for r in rs if int(r["level"])==1), None)
    mx=rs[-1]
    desc1=(lv1 or {}).get("desc"); descM=mx.get("desc")
    eff1=(lv1 or {}).get("effect"); effM=mx.get("effect")
    scales = (desc1 != descM) or (eff1 != effM)
    print(f"id={rid} {rs[0].get('name')!r:16} eff(lv1)={eff1} eff(max)={effM} "
          f"scales={scales}")
    if desc1: print(f"     desc lv1 : {desc1}")
    if descM and descM!=desc1: print(f"     desc max : {descM}")

# distinct effect attr ids used
print("\n=== effect field attr ids ===")
eff_ids=set()
for r in rows:
    for e in (r.get("effect") or []): eff_ids.add(e[0])
attr_names=cat.get("attr_names",{})
for e in sorted(eff_ids):
    print(f"  {e} = {attr_names.get(str(e),'?')}")

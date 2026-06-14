"""Test harness: run control_panel.carpark_tools_js.READ_STATE_JS via raw CDP."""
from __future__ import annotations
import argparse, io, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
from rawcdp import RawCDP  # noqa: E402  (also sets sys.stdout to a UTF-8 wrapper)
from control_panel.carpark_tools_js import READ_STATE_JS, EXEC_STEP_JS  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--exec", default="", help="cat,cell,qty,doup to run EXEC_STEP_JS instead")
    a = ap.parse_args()
    c = RawCDP(a.port, timeout=120.0)
    c.enable_runtime()
    if a.exec == "parse":
        # syntax/parse check only — build the function objects, do NOT call them
        print("READ_STATE_JS:", c.evaluate(f"typeof ({READ_STATE_JS})"))
        print("EXEC_STEP_JS:", c.evaluate(f"typeof ({EXEC_STEP_JS})"))
        c.close(); return
    if a.exec:
        cat, cell, qty, doup = a.exec.split(",")
        args = [int(cat), int(cell), int(qty), doup.strip() in ("1", "true", "True")]
        res = c.evaluate(f"({EXEC_STEP_JS})({json.dumps(args)})")
        print("EXEC:", res)
    else:
        res = c.evaluate(f"({READ_STATE_JS})()")
        data = json.loads(res) if isinstance(res, str) else res
        if data.get("error"):
            print("ERROR:", data["error"]); c.close(); return
        print(f"coin = {data.get('coin')}")
        print(f"owned decorations = {len(data.get('decos', []))}")
        for d in data.get("decos", []):
            nsteps = len(d.get("steps", []))
            first = d["steps"][0] if d.get("steps") else None
            print(f"  id={d['id']:<6} {d['name']!r:18} lv={d['level']:<2} "
                  f"price={d['price']:<7} 限購剩={d['limit_remaining']:<4} "
                  f"remaining_stars={nsteps} next={first}")
    c.close()


if __name__ == "__main__":
    main()

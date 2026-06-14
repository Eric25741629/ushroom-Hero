"""Integration test: live READ_STATE_JS -> routes_carpark_tools._plan."""
from __future__ import annotations
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
from rawcdp import RawCDP  # noqa: E402
from control_panel.carpark_tools_js import READ_STATE_JS  # noqa: E402
from control_panel.routes_carpark_tools import _plan  # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9223
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    c = RawCDP(port, timeout=120.0); c.enable_runtime()
    state = json.loads(c.evaluate(f"({READ_STATE_JS})()")); c.close()
    plan = _plan(state, budget=budget, max_steps=15)
    print(f"coin={plan['coin']:,} budget={plan['budget']:,} owned={plan['owned_count']} "
          f"steps={len(plan['steps'])} total_coin={plan['total_coin']:,} "
          f"total_attr={plan['total_attr']:,}")
    cum = 0
    for i, s in enumerate(plan["steps"], 1):
        cum += s["coin"]
        print(f"  {i:>2}. {s['name']!r:16} cat{s['cat']}/c{s['cell']} "
              f"★{s['from_level']}→{s['to_level']} frags={s['frags']:<3} "
              f"coin={s['coin']:>9,} +attr={s['attr_gain']:<6} "
              f"c/a={s['coin_per_attr']:<7} cum={cum:,}")


if __name__ == "__main__":
    main()

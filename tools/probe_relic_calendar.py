"""TEMP read-only recon: find the 遺物碎片衝刺 (relic sprint) activity END time.

The 6572 act_cross_limited_rank_info reply carries only task_list (no time window).
This probe hunts for where the activity's end timestamp lives so the dashboard can
show 本期活動結束日:
  - sends 6576 act_cross_limit_rank_calendar {} and decodes the reply recursively,
    flagging any int in the plausible Unix-second range as a candidate end_ts;
  - confirms the active act_type via 6572 (probe 269 then 13);
  - optionally scans cocos config/managers for a matching end time (ground truth
    to cross-reference the WS field against).

READ-ONLY (no spend). Delete after recon.

  conda activate mushroom1
  python tools/probe_relic_calendar.py --port 9226
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from ws_token import codec  # noqa: E402

GAME_HOST = "mushroomh5.acenetgame.com"
CMD_SPRINT_INFO = 0x19AC      # 6572
CMD_SPRINT_CALENDAR = 0x19B0  # 6576
ACT_TYPES = (269, 13)

# "now" sanity window for a Unix-second timestamp. The live ad URL showed
# dt=1781880588360 ms (~1.7818e9 s), so flag anything from ~2024 to ~2027.
TS_LO = 1_700_000_000
TS_HI = 1_900_000_000


def _attach(port: int):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in browser.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                return pw, browser, p
    pw.stop()
    raise SystemExit(f"no page matching host={GAME_HOST} on CDP {port}")


def _hex(b, n=240):
    s = " ".join(f"{x:02x}" for x in (b or [])[:n])
    return s + (" ..." if b and len(b) > n else "")


def _call(page, cmd, body, timeout=8.0):
    from utils.web_game_api import WebGameAPI
    return WebGameAPI(page).call_raw(cmd, bytes(body), timeout_sec=timeout)


def _flag(v):
    return "  <-- TS?" if isinstance(v, int) and TS_LO <= v <= TS_HI else ""


def _walk_print(body: bytes, prefix=""):
    """Recursively decode a protobuf body, printing each field; recurse bytes."""
    for fnum, v in codec.walk(body):
        if isinstance(v, (bytes, bytearray)):
            vb = bytes(v)
            # try to recurse; if it parses as nested message show it, else show hex
            nested = None
            try:
                nested = list(codec.walk(vb))
            except Exception:
                nested = None
            if nested:
                print(f"{prefix}#{fnum} (msg, {len(vb)}B):")
                _walk_print(vb, prefix + "    ")
            else:
                print(f"{prefix}#{fnum} (bytes {len(vb)}): {_hex(list(vb), 40)}")
        else:
            print(f"{prefix}#{fnum} = {v}{_flag(v)}")


def _find_active(page):
    for act in ACT_TYPES:
        try:
            body = _call(page, CMD_SPRINT_INFO, codec.pb_uint(1, act))
        except Exception as exc:  # noqa: BLE001
            print(f"  6572 act={act}: error {exc}")
            continue
        d = codec.walk_dict(body)
        ntasks = sum(1 for fnum, _ in codec.walk(body) if fnum == 3)
        print(f"  6572 act={act}: len={len(body)} act_type#1={d.get(1)} "
              f"group#2={d.get(2)} n_task#3={ntasks}")
        if ntasks:
            return act
    return None


# Scan cocos for a config/manager carrying the activity end time (ground truth).
_COCOS_JS = r"""
(act) => {
  const out = {hits: [], cfg_tables: []};
  const isTs = n => typeof n === 'number' && n >= 1700000000 && n <= 1900000000;
  // 1) scan global config tables whose name hints at activity/cross/rank/time
  try {
    for (const k of Object.keys(window)) {
      if (!/config/i.test(k)) continue;
      if (!/(act|cross|rank|limit|time|calendar)/i.test(k)) continue;
      let C; try { C = window[k]; } catch(e){ continue; }
      if (!C || typeof C.getDatas !== 'function') continue;
      let datas; try { datas = C.getDatas(); } catch(e){ continue; }
      const rows = [];
      for (const kk in datas) { if (!datas.hasOwnProperty(kk)) continue;
        const r = datas[kk]; const tsFields = {};
        for (const f in r) if (isTs(r[f])) tsFields[f] = r[f];
        if (Object.keys(tsFields).length) rows.push({key: kk, ts: tsFields, raw: r});
      }
      if (rows.length) out.cfg_tables.push({table: k, rows: rows.slice(0, 12)});
    }
  } catch(e){ out.cfg_err = String(e); }
  // 2) deep-ish scan of window objects for any field holding a TS near an act ref
  try {
    const seen = new Set();
    const scan = (obj, path, depth) => {
      if (!obj || depth > 2 || seen.has(obj)) return;
      if (typeof obj === 'object') seen.add(obj);
      for (const f in obj) {
        let v; try { v = obj[f]; } catch(e){ continue; }
        if (isTs(v) && /(end|close|stop|finish|expire)/i.test(f)) {
          out.hits.push({path: path + '.' + f, val: v});
        } else if (v && typeof v === 'object' && depth < 2 && /(act|rank|cross|sprint|limit)/i.test(f)) {
          scan(v, path + '.' + f, depth + 1);
        }
      }
    };
    for (const k of Object.keys(window)) {
      if (!/(act|rank|cross|sprint|limit|model|mgr|manager|data)/i.test(k)) continue;
      let v; try { v = window[k]; } catch(e){ continue; }
      if (v && typeof v === 'object') scan(v, k, 0);
    }
  } catch(e){ out.scan_err = String(e); }
  out.hits = out.hits.slice(0, 40);
  return out;
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9226)
    args = ap.parse_args()

    pw, _, page = _attach(args.port)
    print(f"[probe] page={page.url}")

    print("\n=== find active act_type (6572) ===")
    act = _find_active(page)
    print(f"  -> active act_type = {act}")

    print("\n=== 6576 act_cross_limit_rank_calendar {} ===")
    for body_desc, req in (("empty", b""),
                           (f"act={act}", codec.pb_uint(1, act) if act else b"")):
        if body_desc.startswith("act") and not act:
            continue
        try:
            rep = _call(page, CMD_SPRINT_CALENDAR, req)
        except Exception as exc:  # noqa: BLE001
            print(f"  req={body_desc}: error {exc}")
            continue
        print(f"  --- req={body_desc} reply len={len(rep)} ---")
        print(f"  hex: {_hex(list(rep))}")
        _walk_print(rep, "    ")

    print("\n=== cocos config/manager scan for end-time (ground truth) ===")
    try:
        info = page.evaluate(_COCOS_JS, act or 0)
        print(json.dumps(info, ensure_ascii=False, indent=2)[:4000])
    except Exception as exc:  # noqa: BLE001
        print(f"  cocos scan error: {exc}")

    pw.stop()


if __name__ == "__main__":
    main()

"""Dump configParking_design (車位裝飾 catalog) from a live H5 device via CDP.

Read-only. Attaches to the already-open Playwright/Chrome on the given CDP port,
finds the game page, and dumps the full (id, level) decoration catalog plus a few
probes (config existence, sample raw row keys, owned skin_list if reachable,
current page screenshot).

Usage:
    python tools/dump_parking_design.py --port 9223 \
        --out docs/protocol/PARKING_DESIGN_CATALOG.json \
        --shot logs/_scratch/parking_now.png
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GAME_HOST = "mushroomh5.acenetgame.com"

# Dump the whole configParking_design table. Defensive about getDatas() shape
# (array vs keyed object vs Map) and about which fields actually exist.
_DUMP_JS = r"""
() => {
  const out = {has_cfg: false, rows: [], count: 0};
  if (typeof configParking_design === 'undefined') return out;
  out.has_cfg = true;
  const cfg = configParking_design;
  let datas = null;
  try {
    if (typeof cfg.getDatas === 'function') datas = cfg.getDatas();
  } catch (e) { out.getDatas_err = String(e); }
  if (datas == null) datas = cfg.datas || cfg._datas || null;
  let arr = [];
  if (Array.isArray(datas)) arr = datas;
  else if (datas && typeof datas.forEach === 'function' && !(datas instanceof Array)) {
    // Map-like
    try { datas.forEach(v => arr.push(v)); } catch (e) { out.map_err = String(e); }
    if (!arr.length && datas) arr = Object.values(datas);
  } else if (datas && typeof datas === 'object') {
    arr = Object.values(datas);
  }
  // Sample raw keys of the first object row so we can confirm field names.
  const sample = arr.find(r => r && typeof r === 'object');
  if (sample) out.sample_keys = Object.keys(sample);
  out.rows = arr.filter(r => r && typeof r === 'object').map(r => ({
    id: r.id, level: r.level, position: r.position, if_initial: r.if_initial,
    expend: r.expend, own_attrs: r.own_attrs, effect: r.effect,
    pvp_effect: r.pvp_effect, pos_occupy: r.pos_occupy,
    power: r.power, name: r.name, desc: r.desc, desc_parm: r.desc_parm,
  }));
  out.count = out.rows.length;
  // Also dump configAttribute id->name so we can label own_attrs[*][0].
  try {
    if (typeof configAttribute !== 'undefined') {
      const ad = configAttribute.getDatas ? configAttribute.getDatas()
                                           : (configAttribute.datas || {});
      const aarr = Array.isArray(ad) ? ad : Object.values(ad || {});
      out.attr_names = {};
      aarr.forEach(a => { if (a && a.id != null) out.attr_names[a.id] = a.name; });
    }
  } catch (e) { out.attr_err = String(e); }
  // Currency goods names for expend[*][0].
  try {
    if (typeof configGoods !== 'undefined') {
      out.goods_names = {};
      out.rows.forEach(r => {
        (r.expend || []).forEach(e => {
          const gid = e && e[0];
          if (gid != null && out.goods_names[gid] === undefined) {
            try { const g = configGoods.getDataByKey(gid);
                  out.goods_names[gid] = g ? g.name : null; } catch (x) {}
          }
        });
      });
    }
  } catch (e) { out.goods_err = String(e); }
  return out;
}
"""

# Best-effort read of what skins I currently own. The decorate view builds a
# {skin_id: skin_lev} map; the underlying data usually lives on a ParkingControl
# singleton. We probe several likely globals without assuming one.
_OWNED_JS = r"""
() => {
  const out = {};
  const tryGet = (label, fn) => { try { out[label] = fn(); } catch (e) { out[label+'_err'] = String(e); } };
  // Walk window for an object exposing skin_list.
  tryGet('window_scan', () => {
    const hits = [];
    for (const k of Object.keys(window)) {
      try {
        const v = window[k];
        if (v && typeof v === 'object' && (Array.isArray(v.skin_list) || v.skinList)) {
          hits.push(k);
        }
      } catch (e) {}
    }
    return hits;
  });
  return out;
}
"""

_PAGE_STATE_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {err: 'no_cc'};
  const scene = cc.director.getScene();
  const norm = (function find(n){
    if (!n || !n.children) return null;
    for (const c of n.children) {
      if (c.name === 'UIRoot') {
        for (const cc2 of c.children) if (cc2.name === 'NormalView') return cc2;
      }
    }
    return null;
  })(scene);
  const active = [];
  if (norm) for (const c of norm.children) if (c.active) active.push(c.name);
  return {normalview_active_children: active};
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--out", default="docs/protocol/PARKING_DESIGN_CATALOG.json")
    ap.add_argument("--shot", default="")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
    page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                page = p
                break
        if page:
            break
    if page is None:
        pw.stop()
        raise SystemExit(f"no page matching host={GAME_HOST} on CDP {args.port}")

    print(f"[dump] page={page.url}")
    state = page.evaluate(_PAGE_STATE_JS)
    print(f"[dump] page_state={json.dumps(state, ensure_ascii=False)}")

    data = page.evaluate(_DUMP_JS)
    print(f"[dump] has_cfg={data.get('has_cfg')} count={data.get('count')}")
    print(f"[dump] sample_keys={data.get('sample_keys')}")
    print(f"[dump] goods_names={json.dumps(data.get('goods_names', {}), ensure_ascii=False)}")
    print(f"[dump] attr_names_count={len(data.get('attr_names', {}) or {})}")

    owned = page.evaluate(_OWNED_JS)
    print(f"[dump] owned_probe={json.dumps(owned, ensure_ascii=False)}")

    if args.shot:
        try:
            page.screenshot(path=args.shot)
            print(f"[dump] screenshot -> {args.shot}")
        except Exception as e:  # noqa: BLE001
            print(f"[dump] screenshot failed: {e}")

    payload = {"page_state": state, "owned_probe": owned, **data}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[dump] wrote {args.out}")

    pw.stop()


if __name__ == "__main__":
    main()

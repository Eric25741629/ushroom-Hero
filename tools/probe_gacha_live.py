"""Live recon of the gacha (抽卡/召喚) pages via CDP — for protocol decode.

Subcommands:
  setup  — attach, install idempotent WS ring-buffer probe, screenshot, and
           inspect the scene: active *View under NormalView + every node whose
           Label contains 召喚/抽卡/次, with the nearest cc.Button ancestor path.
  drain  — return + clear the WS ring; summarize cmds and dump TX bodies (hex).
  click  — clear baseline -> emit('click') node by path -> settle -> drain ->
           diff (cmds new/elevated post-click) + dump every TX frame body (hex).

Read-only by default (setup/drain). `click` triggers the real button and may
spend in-game currency — use deliberately.

  conda activate mushroom1
  python tools/probe_gacha_live.py setup  --port 9226
  python tools/probe_gacha_live.py click  --port 9226 --path "/UIRoot/.../btnXxx"
  python tools/probe_gacha_live.py drain  --port 9226
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime

# Allow `from utils...` when run as `python tools/probe_gacha_live.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GAME_HOST = "mushroomh5.acenetgame.com"
RING_MAX = 4000
MAX_BODY_BYTES = 65536

_INSTALL_JS = r"""
([ringMax, maxBodyBytes]) => {
  const nm = window.netManager;
  if (!nm || !nm._cnet) return 'no_cnet';
  const sock = nm._cnet;
  if (typeof sock.sendMessage !== 'function' || typeof sock.reciveMsg !== 'function') return 'no_methods';
  const RING_MAX = ringMax | 0 || 4000;
  const MAX = maxBodyBytes | 0 || 65536;
  if (window.__probe_inst) { window.__probe_ring = []; return 'reinstalled'; }
  window.__probe_ring = [];
  window.__probe_seq = 0;
  const lenOf = b => !b ? 0 : (typeof b.byteLength === 'number' ? b.byteLength : (b.length | 0));
  const toArr = (body) => {
    if (!body) return null;
    let bytes;
    try {
      if (body instanceof Uint8Array) bytes = body;
      else if (body.buffer) bytes = new Uint8Array(body.buffer, body.byteOffset || 0, body.byteLength);
      else if (Array.isArray(body)) bytes = Uint8Array.from(body);
      else return null;
    } catch (e) { return null; }
    const cap = Math.min(bytes.length, MAX);
    const arr = new Array(cap);
    for (let i = 0; i < cap; i++) arr[i] = bytes[i];
    return arr;
  };
  const push = (cmd, dir_, body) => {
    const buf = window.__probe_ring;
    buf.push({seq: ++window.__probe_seq, cmd: cmd | 0, dir: dir_, ts: Date.now(),
              full_len: lenOf(body), body: toArr(body)});
    if (buf.length > RING_MAX) buf.splice(0, buf.length - RING_MAX);
  };
  const origSend = sock.sendMessage.bind(sock);
  sock.sendMessage = function (cmd, body) { try { push(cmd, 'tx', body); } catch(e){} return origSend(cmd, body); };
  const origRecv = sock.reciveMsg.bind(sock);
  sock.reciveMsg = function (cmd, body) { try { push(cmd, 'rx', body); } catch(e){} return origRecv(cmd, body); };
  window.__probe_inst = true;
  return 'installed';
}
"""

_CLEAR_JS = r"() => { window.__probe_ring = []; return 'cleared'; }"
_DRAIN_JS = r"() => { const b = window.__probe_ring || []; window.__probe_ring = []; return b; }"

# Scene inspect: active NormalView children + label-matched nodes with button paths.
_INSPECT_JS = r"""
() => {
  if (typeof cc === 'undefined' || !cc.director) return {err: 'no_cc'};
  const scene = cc.director.getScene();
  const out = {active_views: [], hits: []};

  const pathOf = (n) => { const parts = []; let c = n;
    while (c && c.name !== undefined) { parts.unshift(c.name); if (!c.parent) break; c = c.parent; }
    return '/' + parts.join('/'); };
  const labelOf = (n) => { for (const c of (n._components||[]))
    if (c && typeof c.string === 'string' && c.string.length) return c.string; return null; };
  const hasButton = (n) => { try { return !!(n.getComponent && n.getComponent('cc.Button')); } catch(e){ return false; } };
  const nearestButton = (n) => { let c = n;
    for (let i=0; i<8 && c; i++) { if (hasButton(c)) return c; c = c.parent; } return null; };

  // active *View under UIRoot/NormalView
  try {
    const find = (n, nm) => n && n.children && n.children.find(c => c.name === nm);
    const norm = find(find(scene, 'UIRoot'), 'NormalView');
    if (norm) for (const c of norm.children)
      if (c.active && /View$/.test(c.name)) out.active_views.push(c.name);
  } catch(e){ out.view_err = String(e); }

  // label search
  const KW = ['召喚', '抽卡', '抽獎', '次', '券', '招募', '祈願'];
  const seen = new Set();
  const walk = (n, d) => {
    if (!n || d > 16 || !n.active) return;
    const s = labelOf(n);
    if (s && KW.some(k => s.includes(k))) {
      const btn = nearestButton(n);
      const bp = btn ? pathOf(btn) : null;
      if (!bp || !seen.has(bp + '|' + s)) {
        if (bp) seen.add(bp + '|' + s);
        out.hits.push({label: s, label_path: pathOf(n),
                       button_path: bp, button_name: btn ? btn.name : null});
      }
    }
    for (const c of (n.children||[])) walk(c, d+1);
  };
  walk(scene, 0);
  return out;
}
"""

_FIND_JS = r"""
([kws, names]) => {
  if (typeof cc === 'undefined' || !cc.director) return {err: 'no_cc'};
  const scene = cc.director.getScene();
  const out = {label_hits: [], name_hits: [], active_views: []};
  const pathOf = (n) => { const parts = []; let c = n;
    while (c && c.name !== undefined) { parts.unshift(c.name); if (!c.parent) break; c = c.parent; }
    return '/' + parts.join('/'); };
  const labelOf = (n) => { for (const c of (n._components||[]))
    if (c && typeof c.string === 'string' && c.string.length) return c.string; return null; };
  const hasButton = (n) => { try { return !!(n.getComponent && n.getComponent('cc.Button')); } catch(e){ return false; } };
  const nearestButton = (n) => { let c = n;
    for (let i=0;i<8&&c;i++){ if(hasButton(c)) return c; c=c.parent; } return null; };
  try {
    const find = (n, nm) => n && n.children && n.children.find(c => c.name === nm);
    const norm = find(find(scene, 'UIRoot'), 'NormalView');
    if (norm) for (const c of norm.children)
      if (c.active && /View$/.test(c.name)) out.active_views.push(c.name);
  } catch(e){}
  const seenL = new Set(), seenN = new Set();
  const walk = (n, d) => {
    if (!n || d > 18 || !n.active) return;
    const s = labelOf(n);
    if (s && kws.some(k => s.includes(k))) {
      const btn = nearestButton(n); const bp = btn ? pathOf(btn) : null;
      const key = (bp||pathOf(n)) + '|' + s;
      if (!seenL.has(key)) { seenL.add(key);
        out.label_hits.push({label:s, button_path:bp, button_name:btn?btn.name:null, label_path:pathOf(n)}); }
    }
    const nm = n.name || '';
    if (names.some(k => nm.toLowerCase().includes(k.toLowerCase()))) {
      const p = pathOf(n);
      if (!seenN.has(p)) { seenN.add(p);
        out.name_hits.push({name:nm, path:p, active:!!n.active, has_button:hasButton(n), label:labelOf(n)}); }
    }
    for (const c of (n.children||[])) walk(c, d+1);
  };
  walk(scene, 0);
  return out;
}
"""

_CLICK_JS = r"""
([path]) => {
  if (typeof cc === 'undefined' || !cc.director) return {clicked: false, err: 'no_cc'};
  const scene = cc.director.getScene();
  const find = (root, parts) => { let n = root;
    for (const p of parts) { if (!n || !n.children) return null;
      n = n.children.find(c => (c.name || '') === p); if (!n) return null; } return n; };
  const node = find(scene, path.split('/').filter(Boolean));
  if (!node) return {clicked: false, err: 'not_found', path};
  let did = false;
  try { if (node.emit) { node.emit('click', node); did = true; } } catch(e){}
  try { const b = node.getComponent && node.getComponent('cc.Button');
    if (b && b.clickEvents) for (const ev of b.clickEvents) {
      const t = ev.target, cn = ev._componentName || ev.component, h = ev.handler;
      const comp = t && t.getComponent && t.getComponent(cn);
      if (comp && typeof comp[h] === 'function') { comp[h](null, ev.customEventData); did = true; }
    } } catch(e){}
  return {clicked: did, name: node.name, active: !!node.active, path};
}
"""


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


def _hex(body, n=None):
    body = body or []
    if n is not None:
        body = body[:n]
    return " ".join(f"{b:02x}" for b in body)


def _fmt(e, preview=None):
    ts = datetime.fromtimestamp(e["ts"] / 1000).strftime("%H:%M:%S.%f")[:-3]
    return (f"#{e['seq']:>4} {ts} {e['dir']} cmd=0x{e['cmd']:04x} "
            f"({e['cmd']}) len={e['full_len']:>4}  {_hex(e.get('body'), preview)}")


def cmd_setup(port: int) -> None:
    pw, _, page = _attach(port)
    print(f"[probe] page={page.url}")
    print(f"[probe] install: {page.evaluate(_INSTALL_JS, [RING_MAX, MAX_BODY_BYTES])}")
    shot = "logs/gacha_setup.png"
    try:
        page.screenshot(path=shot)
        print(f"[probe] screenshot -> {shot}")
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] screenshot failed: {exc}")
    insp = page.evaluate(_INSPECT_JS)
    print("[probe] inspect:")
    print(json.dumps(insp, ensure_ascii=False, indent=2))
    pw.stop()


def cmd_drain(port: int, preview: int | None) -> None:
    pw, _, page = _attach(port)
    entries = page.evaluate(_DRAIN_JS) or []
    pw.stop()
    print(f"[probe] drained {len(entries)} frames")
    counts = Counter((int(e["cmd"]), e["dir"]) for e in entries)
    print("=== summary ===")
    for (cmd, dir_), n in sorted(counts.items()):
        print(f"  cmd=0x{cmd:04x} ({cmd}) {dir_} x{n}")
    print("=== TX frames ===")
    for e in entries:
        if e["dir"] == "tx":
            print(_fmt(e, preview))


def cmd_find(port: int, kws: list[str], names: list[str]) -> None:
    pw, _, page = _attach(port)
    res = page.evaluate(_FIND_JS, [kws, names])
    pw.stop()
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_send(port: int, cmd_id: int, type_: int, count: int) -> None:
    pw, _, page = _attach(port)
    from utils.web_game_api import WebGameAPI, _encode_varint
    body = b"\x08" + _encode_varint(type_) + b"\x10" + _encode_varint(count)
    print(f"[send] cmd=0x{cmd_id:04x} body={body.hex(' ')} (type={type_}, count={count})")
    api = WebGameAPI(page)
    try:
        resp = api.call_raw(cmd_id, body, timeout_sec=10)
        print(f"[send] rx len={len(resp)}  {resp[:48].hex(' ')}")
        # decode top-level fields to confirm result shape
        from utils.protobuf_walk import walk_fields
        items = 0
        for fnum, wire, val in walk_fields(resp):
            if wire == 2 and isinstance(val, (bytes, bytearray)):
                items += 1
        print(f"[send] rx repeated-bytes groups (likely drawn items) = {items}")
    except Exception as exc:  # noqa: BLE001
        print(f"[send] ERROR: {exc}")
    try:
        page.screenshot(path="logs/gacha_after_send.png")
    except Exception:  # noqa: BLE001
        pass
    pw.stop()


def cmd_drawjs(port: int, type_: int, count: int) -> None:
    """Run the EXACT dashboard payload control_panel.gacha_tools_js.DRAW_ONCE_JS
    via CDP — same code path the /api/gacha/draw route uses."""
    from control_panel.gacha_tools_js import DRAW_ONCE_JS
    ticket = {1: 1012, 2: 1013}.get(type_, 0)
    pw, _, page = _attach(port)
    expr = f"({DRAW_ONCE_JS})({json.dumps([type_, count, ticket, 6000])})"
    res = page.evaluate(expr)
    print(f"[drawjs] type={type_} count={count} ticket={ticket} -> {res}")
    pw.stop()


_GOODS_JS = r"""
(ids) => {
  const out = {fns_present: [], values: {}};
  const cand = ['getGoodsCountByGoodsGtid','getGoodsCount','getGoodsNum','getItemCount','getGoodsCountById','getGoodsCountByGtid'];
  out.fns_present = cand.filter(f => typeof window[f] === 'function');
  // also scan window objects for a method exposing goods counts
  const objHits = [];
  try {
    for (const k of Object.keys(window)) {
      let v; try { v = window[k]; } catch(e){ continue; }
      if (v && typeof v === 'object') {
        for (const m of cand) if (typeof v[m] === 'function') objHits.push(k + '.' + m);
      }
    }
  } catch(e){}
  out.obj_hits = objHits.slice(0, 30);
  for (const id of ids) {
    out.values[id] = {};
    for (const f of out.fns_present) { try { out.values[id][f] = window[f](id); } catch(e){ out.values[id][f] = 'ERR:'+e; } }
  }
  return out;
}
"""


def cmd_goods(port: int, ids: list[int]) -> None:
    pw, _, page = _attach(port)
    res = page.evaluate(_GOODS_JS, ids)
    pw.stop()
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_bagdiff(port: int, type_: int, count: int) -> None:
    """Identify the draw-ticket item_id: send a draw, then cross-reference the
    0x0402 inventory-change push against the 0x0902 result's drawn item_ids.
    The changed item that is NOT a drawn reward = the ticket/currency spent."""
    from utils.web_game_api import WebGameAPI, _encode_varint, parse_inventory_push
    from utils.protobuf_walk import walk_fields
    pw, _, page = _attach(port)
    page.evaluate(_INSTALL_JS, [RING_MAX, MAX_BODY_BYTES])
    page.evaluate(_CLEAR_JS)
    body = b"\x08" + _encode_varint(type_) + b"\x10" + _encode_varint(count)
    api = WebGameAPI(page)
    resp = api.call_raw(0x0902, body, timeout_sec=10)
    time.sleep(0.8)
    frames = page.evaluate(_DRAIN_JS) or []
    pw.stop()
    drawn = set()
    for fnum, wire, val in walk_fields(resp):
        if fnum == 2 and wire == 2:
            for sf, sw, sv in walk_fields(val):
                if sf == 1 and sw == 0:
                    drawn.add(sv)
    print(f"[bagdiff] type={type_} count={count}  drawn reward ids={sorted(drawn)}")
    print("=== 0x0402 changed items ===")
    for e in frames:
        if e["dir"] == "rx" and int(e["cmd"]) == 0x0402:
            inv = parse_inventory_push(bytes(e.get("body") or []))
            for it in inv["items"]:
                iid = it["item_id"]
                mark = "drawn-reward" if iid in drawn else "<<< TICKET/CURRENCY candidate"
                print(f"  id={iid} qty={it['qty']} raw={it['raw']}  {mark}")


def cmd_click(port: int, path: str, settle_ms: int, baseline_ms: int) -> None:
    pw, _, page = _attach(port)
    page.evaluate(_CLEAR_JS)
    time.sleep(baseline_ms / 1000.0)
    baseline = page.evaluate(_DRAIN_JS) or []
    res = page.evaluate(_CLICK_JS, [path])
    print(f"[probe] click {path} -> {res}")
    time.sleep(settle_ms / 1000.0)
    post = page.evaluate(_DRAIN_JS) or []
    try:
        page.screenshot(path="logs/gacha_after_click.png")
    except Exception:  # noqa: BLE001
        pass
    pw.stop()

    base_counts = Counter((int(e["cmd"]), e["dir"]) for e in baseline)
    post_counts = Counter((int(e["cmd"]), e["dir"]) for e in post)
    print("=== diff (cmds new/elevated post-click) ===")
    for key, n in sorted(post_counts.items()):
        bl = base_counts.get(key, 0)
        if bl == 0 or n > max(bl * 1.5, bl + 2):
            cmd, dir_ = key
            print(f"  cmd=0x{cmd:04x} ({cmd}) {dir_} post={n} baseline={bl}")
    print("=== post-click TX frames (full body hex) ===")
    for e in post:
        if e["dir"] == "tx":
            print(_fmt(e))
    print("=== post-click RX frames (first 32 bytes) ===")
    for e in post:
        if e["dir"] == "rx":
            print(_fmt(e, 32))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ps = sub.add_parser("setup"); ps.add_argument("--port", type=int, default=9226)
    pd = sub.add_parser("drain"); pd.add_argument("--port", type=int, default=9226)
    pd.add_argument("--preview", type=int, default=None)
    pc = sub.add_parser("click"); pc.add_argument("--port", type=int, default=9226)
    pc.add_argument("--path", required=True)
    pc.add_argument("--settle-ms", type=int, default=2800)
    pc.add_argument("--baseline-ms", type=int, default=800)
    pf = sub.add_parser("find"); pf.add_argument("--port", type=int, default=9226)
    pf.add_argument("--kw", default="")
    pf.add_argument("--names", default="")
    pse = sub.add_parser("send"); pse.add_argument("--port", type=int, default=9226)
    pse.add_argument("--cmd-id", default="0x0902")
    pse.add_argument("--type", type=int, required=True)
    pse.add_argument("--count", type=int, required=True)
    pdj = sub.add_parser("drawjs"); pdj.add_argument("--port", type=int, default=9226)
    pdj.add_argument("--type", type=int, required=True)
    pdj.add_argument("--count", type=int, required=True)
    pbd = sub.add_parser("bagdiff"); pbd.add_argument("--port", type=int, default=9226)
    pbd.add_argument("--type", type=int, required=True)
    pbd.add_argument("--count", type=int, default=15)
    pg = sub.add_parser("goods"); pg.add_argument("--port", type=int, default=9226)
    pg.add_argument("--ids", default="1012,1013")
    args = ap.parse_args()

    if args.cmd == "setup":
        cmd_setup(args.port)
    elif args.cmd == "drain":
        cmd_drain(args.port, args.preview)
    elif args.cmd == "click":
        cmd_click(args.port, args.path, args.settle_ms, args.baseline_ms)
    elif args.cmd == "find":
        kws = [k for k in args.kw.split(",") if k]
        names = [n for n in args.names.split(",") if n]
        cmd_find(args.port, kws, names)
    elif args.cmd == "send":
        cid = int(args.cmd_id, 16) if args.cmd_id.lower().startswith("0x") else int(args.cmd_id)
        cmd_send(args.port, cid, args.type, args.count)
    elif args.cmd == "drawjs":
        cmd_drawjs(args.port, args.type, args.count)
    elif args.cmd == "bagdiff":
        cmd_bagdiff(args.port, args.type, args.count)
    elif args.cmd == "goods":
        cmd_goods(args.port, [int(x) for x in args.ids.split(",") if x.strip()])


if __name__ == "__main__":
    main()

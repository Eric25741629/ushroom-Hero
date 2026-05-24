"""Sea (航海) sub-game investigation probe via CDP.

Subcommands:
  search <kw...>     find nodes by name OR label substring (active only unless --all)
  tree <path>        dump subtree at path (depth configurable)
  click <path>       emit('click') on node at path; fallback to clickEvents
  text               dump all visible Label strings with screen positions
  hook               install idempotent WS ring buffer
  drain              return + clear WS ring
  shot <out>         screenshot

Path syntax: '/Canvas/UIRoot/...'; node matched by walking child names.
"""
from __future__ import annotations
import argparse, io, json, sys, time
from pathlib import Path
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

GAME_HOST = "mushroomh5.acenetgame.com"

HOOK_JS = r"""
() => {
  if (window.__sea_hook) return "already";
  const nm = window.netManager;
  if (!nm || !nm._cnet) return "no-netManager";
  window.__sea_ring = window.__sea_ring || [];
  const ring = window.__sea_ring;
  const push = (dir, cmd, body) => {
    let n = 0; try { n = body && body.length ? body.length : (body && body.byteLength) || 0; } catch(e){}
    ring.push({dir, cmd, n, ts: Date.now()});
    if (ring.length > 4000) ring.shift();
  };
  const c = nm._cnet;
  const origSend = c.sendMessage && c.sendMessage.bind(c);
  const origRecv = c.reciveMsg && c.reciveMsg.bind(c);
  if (origSend) c.sendMessage = function(cmd, body){ try{push('tx', cmd, body);}catch(e){} return origSend(cmd, body); };
  if (origRecv) c.reciveMsg = function(cmd, body){ try{push('rx', cmd, body);}catch(e){} return origRecv(cmd, body); };
  window.__sea_hook = true;
  return "installed";
}
"""

DRAIN_JS = r"""
() => {
  const r = window.__sea_ring || [];
  const out = r.map(e => ({dir: e.dir, cmd: '0x' + (e.cmd>>>0).toString(16), n: e.n, ts: e.ts}));
  window.__sea_ring = [];
  return out;
}
"""

SEARCH_JS = r"""
([kws, includeInactive]) => {
  const hits = [];
  const walk = (n, path, d) => {
    if (!n || d > 16) return;
    if (!includeInactive && !n.active) return;
    const nm = (n.name || '');
    let label = null;
    const comps = n._components || [];
    for (const cc of comps) {
      const s = cc && (cc.string !== undefined ? cc.string : cc._string);
      if (typeof s === 'string' && s.length) { label = s; break; }
    }
    const hay = (nm + ' ' + (label||'')).toLowerCase();
    if (kws.some(k => hay.includes(k.toLowerCase()))) {
      // world position
      let wp = null;
      try { const v = n.worldPosition; wp = {x: Math.round(v.x), y: Math.round(v.y)}; } catch(e){}
      hits.push({path, name: nm, label, active: n.active, type: n.constructor && n.constructor.name, wp, kids: (n.children||[]).length});
    }
    (n.children||[]).forEach(c => walk(c, path + '/' + (c.name||'?'), d+1));
  };
  walk(cc.director.getScene(), '', 0);
  return hits.slice(0, 120);
}
"""

TREE_JS = r"""
([path, maxD]) => {
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) {
      if (!p) continue;
      if (!n.children) return null;
      const c = n.children.find(k => (k.name||'') === p);
      if (!c) return null;
      n = c;
    }
    return n;
  };
  const parts = path.split('/');
  const start = path === '' || path === '/' ? cc.director.getScene() : find(cc.director.getScene(), parts);
  if (!start) return {error: 'not found: ' + path};
  const walk = (n, d) => {
    let label = null;
    for (const c of (n._components||[])) { const s = c && (c.string!==undefined?c.string:c._string); if (typeof s==='string' && s.length){label=s;break;} }
    let wp=null; try{const v=n.worldPosition; wp={x:Math.round(v.x),y:Math.round(v.y)};}catch(e){}
    const o = {name:n.name, active:n.active, type:n.constructor&&n.constructor.name, label, wp};
    if (d < maxD && n.children && n.children.length) o.kids = n.children.map(c => walk(c, d+1));
    else if (n.children && n.children.length) o.nkids = n.children.length;
    return o;
  };
  return walk(start, 0);
}
"""

CLICK_JS = r"""
([path]) => {
  const find = (root, parts) => {
    let n = root;
    for (const p of parts) { if(!p) continue; if(!n.children) return null; const c=n.children.find(k=>(k.name||'')===p); if(!c) return null; n=c; }
    return n;
  };
  const n = find(cc.director.getScene(), path.split('/'));
  if (!n) return 'not found';
  try { n.emit('click', n); } catch(e) { return 'emit-err:'+e; }
  // also try button clickEvents fallback
  try {
    const btn = n.getComponent && (n.getComponent('cc.Button') || n.getComponent('Button'));
    if (btn && btn.clickEvents) {
      for (const ev of btn.clickEvents) {
        const t = ev.target, cn = ev._componentName||ev.component, h = ev.handler, dt = ev.customEventData;
        const comp = t && t.getComponent && t.getComponent(cn);
        if (comp && typeof comp[h]==='function') comp[h](null, dt);
      }
    }
  } catch(e){}
  return 'clicked';
}
"""

TEXT_JS = r"""
() => {
  const out = [];
  const walk = (n) => {
    if (!n || !n.active) return;
    for (const c of (n._components||[])) {
      const s = c && (c.string!==undefined?c.string:c._string);
      if (typeof s==='string' && s.trim().length) {
        let wp=null; try{const v=n.worldPosition; wp={x:Math.round(v.x),y:Math.round(v.y)};}catch(e){}
        out.push({t:s.trim(), wp, name:n.name});
        break;
      }
    }
    (n.children||[]).forEach(walk);
  };
  walk(cc.director.getScene());
  return out;
}
"""


def get_page(pw, port):
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    for ctx in browser.contexts:
        for p in ctx.pages:
            if GAME_HOST in (p.url or "") and "/pwa-sw" not in p.url:
                return p
    raise SystemExit("game page not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9230)
    ap.add_argument("cmd", choices=["search", "tree", "click", "text", "hook", "drain", "shot"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--all", action="store_true", help="include inactive nodes in search")
    ap.add_argument("--depth", type=int, default=3)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        page = get_page(pw, args.port)
        if args.cmd == "search":
            res = page.evaluate(SEARCH_JS, [args.args, args.all])
        elif args.cmd == "tree":
            res = page.evaluate(TREE_JS, [args.args[0] if args.args else "", args.depth])
        elif args.cmd == "click":
            res = page.evaluate(CLICK_JS, [args.args[0]])
        elif args.cmd == "text":
            res = page.evaluate(TEXT_JS)
        elif args.cmd == "hook":
            res = page.evaluate(HOOK_JS)
        elif args.cmd == "drain":
            res = page.evaluate(DRAIN_JS)
        elif args.cmd == "shot":
            out = args.args[0]
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=out, full_page=False)
            res = f"saved {out}"
        print(json.dumps(res, ensure_ascii=False, indent=2) if not isinstance(res, str) else res)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Probe: click outlinePopView btnAd (2小時收益) on 7fe98fc6 and capture WS diff."""
import time, json
from playwright.sync_api import sync_playwright

INSTALL = r"""
() => {
  if (window.__probe_ring) return 'already';
  window.__probe_ring = [];
  const cnet = window.netManager && window.netManager._cnet;
  if (!cnet) return 'no cnet';
  const push = (dir, cmd, body) => {
    let hex = '';
    try { hex = Array.from(new Uint8Array(body || [])).slice(0, 4096)
      .map(b => b.toString(16).padStart(2,'0')).join(''); } catch (e) {}
    window.__probe_ring.push({dir, cmd, hex, ts: Date.now()});
    if (window.__probe_ring.length > 500) window.__probe_ring.shift();
  };
  const send0 = cnet.sendMessage.bind(cnet);
  cnet.sendMessage = function(cmd, body, ...rest) { push('tx', cmd, body); return send0(cmd, body, ...rest); };
  const recv0 = cnet.reciveMsg.bind(cnet);
  cnet.reciveMsg = function(cmd, body, ...rest) { push('rx', cmd, body); return recv0(cmd, body, ...rest); };
  return 'installed';
}
"""
DRAIN = "() => { const r = window.__probe_ring || []; window.__probe_ring = []; return r; }"

CLICK = r"""
(path) => {
  const parts = path.split('/').filter(Boolean);
  let n = cc.director.getScene();
  for (const p of parts) {
    n = (n.children || []).find(c => c.name === p);
    if (!n) return 'not found at ' + p;
  }
  n.emit('click', n);
  const btn = n.getComponent && n.getComponent('cc.Button');
  if (btn && btn.clickEvents && btn.clickEvents.length) {
    for (const ev of btn.clickEvents) {
      try {
        const comp = ev.target.getComponent(ev._componentName || ev.component);
        if (comp && typeof comp[ev.handler] === 'function') comp[ev.handler](null, ev.customEventData);
      } catch (e) { return 'clickEvents err: ' + e; }
    }
    return 'emitted + clickEvents';
  }
  return 'emitted';
}
"""

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9226")
    page = next(p for ctx in browser.contexts for p in ctx.pages
                if "mushroomh5" in (p.url or "") and "/pwa-sw" not in p.url)
    print("install:", page.evaluate(INSTALL))
    page.evaluate(DRAIN)
    time.sleep(1.0)
    base = page.evaluate(DRAIN)
    print("baseline cmds:", sorted({f"0x{m['cmd']:04x}" for m in base}))
    r = page.evaluate(CLICK, "/UIRoot/NormalView/outlinePopView/root/content/btnAd")
    print("click:", r)
    time.sleep(3.0)
    post = page.evaluate(DRAIN)
    basecmds = {m['cmd'] for m in base}
    for m in post:
        tag = '' if m['cmd'] in basecmds else '  <-- NEW'
        print(f"{m['dir']} 0x{m['cmd']:04x} ({m['cmd']}) body={m['hex'][:120]}{tag}")
    page.screenshot(path="tools/tmp_2h_post.png")

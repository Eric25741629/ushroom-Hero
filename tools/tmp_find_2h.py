# -*- coding: utf-8 -*-
"""Find the 2-hour quick-hangup button in cocos scene (device 7fe98fc6, CDP 9226)."""
import sys, json
from playwright.sync_api import sync_playwright

JS = r"""
() => {
  const KW = ['小時','小时','收益','快速','挂机','掛機','獎勵','奖励'];
  const hits = [];
  const walk = (n, path, d) => {
    if (!n || d > 14) return;
    const labels = (n._components || []).map(c => c && c.string).filter(s => typeof s === 'string');
    const matchLabel = labels.find(s => KW.some(k => s.includes(k)));
    const matchName = KW.some(k => (n.name||'').includes(k));
    if (matchName || matchLabel) hits.push({path: path, name: n.name, active: n.active, label: matchLabel});
    (n.children || []).forEach(k => walk(k, path + '/' + (k.name || '?'), d+1));
  };
  walk(cc.director.getScene(), '', 0);
  return hits;
}
"""

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9226")
    page = next(p for ctx in browser.contexts for p in ctx.pages
                if "mushroomh5" in (p.url or "") and "/pwa-sw" not in p.url)
    print(page.url)
    hits = page.evaluate(JS)
    print(json.dumps(hits, ensure_ascii=False, indent=1))

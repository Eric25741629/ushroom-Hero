"""Read-only CDP recon for fly-pet icon extraction.

Attaches to the live H5 game (device 7fe98fc6 = 小寶帳號, CDP 9226), inspects how
fly-pet icons are sourced so we can decide the canvas-dump mapping:
  1. configFly full row fields (does a row point at a sprite/res?)
  2. bundle asset paths matching /fly|pet/  (the spriteFrame naming convention)
  3. FlyPetDataCache presence + a sample pet's fields

Does NOT navigate or click — pure inspection, safe to run while the bot owns the page.

Run:
  & "C:\\Users\\Eric\\.conda\\envs\\mushroom1\\python.exe" tools\\flypet_icon_recon.py
"""
import json
import sys

from playwright.sync_api import sync_playwright

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9226
HOST = "mushroomh5.acenetgame.com"

RECON_JS = r"""
() => {
  const out = {};
  try { out.ccVersion = (typeof cc!=='undefined') ? (cc.ENGINE_VERSION||'unknown') : 'no-cc'; } catch(e){ out.cc_err=String(e); }

  // --- configFly: full field set of a sample row ---
  try {
    if (typeof configFly !== 'undefined') {
      out.configFly_exists = true;
      const datas = configFly.datas || configFly._datas || null;
      const keys = datas ? Object.keys(datas) : [];
      out.configFly_count = keys.length;
      out.configFly_firstKeys = keys.slice(0, 8);
      if (keys.length) {
        const k = keys[0];
        let row = null;
        try { row = configFly.getDataByKey ? configFly.getDataByKey(k) : datas[k]; } catch(e){ out.row_err = String(e); }
        out.sampleKey = k;
        out.sampleRow = row;
        out.sampleRowKeys = row ? Object.keys(row) : null;
      }
    } else { out.configFly_exists = false; }
  } catch(e){ out.configFly_err = String(e); }

  // --- bundle asset paths matching fly|pet ---
  try {
    const hits = {};
    cc.assetManager.bundles.forEach((b, name) => {
      const cfg = b.config || b._config;
      const paths = cfg && cfg.paths;
      const map = paths && (paths._map || paths);
      if (!map) return;
      const matched = [];
      for (const key in map) { if (/fly|pet/i.test(key)) matched.push(key); }
      if (matched.length) hits[name] = matched.slice(0, 80);
    });
    out.assetPaths = hits;
  } catch(e){ out.assetPaths_err = String(e); }

  // --- FlyPetDataCache + a sample owned pet ---
  try {
    let cache = null;
    if (typeof IS !== 'undefined' && typeof ISInclude !== 'undefined') {
      try { cache = IS(ISInclude.FlyPetDataCache); } catch(e){ out.cache_lookup_err = String(e); }
    }
    if (cache) {
      out.flyPetCache = true;
      const list = cache.pet_list || cache.petList || null;
      out.flyPetCount = list ? list.length : null;
      if (list && list.length) {
        const p = list[0];
        out.samplePetKeys = Object.keys(p);
        out.samplePet = { id: p.id, config_id: p.config_id, name: p.name, display_name: p.display_name };
      }
    } else { out.flyPetCache = false; }
  } catch(e){ out.flyPetCache_err = String(e); }

  return out;
}
"""


def main():
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        pages = [p for ctx in browser.contexts for p in ctx.pages]
        urls = [p.url for p in pages]
        game = None
        for p in pages:
            u = p.url or ""
            if HOST in u and "/pwa-sw" not in u:
                game = p
                break
        result = {"port": PORT, "pages": urls, "picked": game.url if game else None}
        if game:
            try:
                result["recon"] = game.evaluate(RECON_JS)
            except Exception as e:
                result["recon_err"] = repr(e)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

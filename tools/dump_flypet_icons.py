"""Dump real in-game fly-pet icons from the live H5 game into a server cache.

Each fly-pet species has a SpriteFrame in bundle-res at:
    ui/atlas/icon_flypet/fly_{config_id}
We attach via CDP, load each frame, draw it to an offscreen canvas, and read back
a PNG dataURL, then write static/flypet_icons/{config_id}.png on disk. The Flask
endpoint /api/fly_pet_icon/<config_id> serves these.

Modes:
  probe [config_id]   -- extract ONE icon, print diagnostics (no file written by default)
  dump                -- enumerate all fly_<id> frames and write every PNG to the cache

Run:
  & "C:\\Users\\Eric\\.conda\\envs\\mushroom1\\python.exe" tools\\dump_flypet_icons.py probe 1001
  & "C:\\Users\\Eric\\.conda\\envs\\mushroom1\\python.exe" tools\\dump_flypet_icons.py dump
"""
import base64
import json
import os
import sys

from playwright.sync_api import sync_playwright

PORT = 9226
HOST = "mushroomh5.acenetgame.com"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(REPO, "static", "flypet_icons")

# Returns {ok, ids:[...]} -- every config_id that has an icon SpriteFrame.
ENUM_JS = r"""
() => {
  const ids = [];
  try {
    cc.assetManager.bundles.forEach((b) => {
      const cfg = b.config || b._config;
      const map = cfg && cfg.paths && (cfg.paths._map || cfg.paths);
      if (!map) return;
      for (const key in map) {
        const m = /^ui\/atlas\/icon_flypet\/fly_(\d+)$/.exec(key);
        if (m) ids.push(parseInt(m[1], 10));
      }
    });
  } catch (e) { return {ok:false, err:String(e)}; }
  return {ok:true, ids: Array.from(new Set(ids)).sort((a,b)=>a-b)};
}
"""

# Loads ui/atlas/icon_flypet/fly_<id>, re-fetches its (same-origin) atlas PNG via
# nativeUrl, and crops the frame rect to a PNG dataURL. The GPU-uploaded texture has
# no CPU image, so we go back to the original atlas file (cached, same-origin -> the
# canvas is not tainted and toDataURL works).
# diag=true -> still draws, but returns only diagnostics + dataURL length (not the blob).
EXTRACT_JS = r"""
async (args) => {
  const {cfgId, diag} = args;
  const path = 'ui/atlas/icon_flypet/fly_' + cfgId;
  let bundle = null;
  cc.assetManager.bundles.forEach((b) => {
    const cfg = b.config || b._config;
    const map = cfg && cfg.paths && (cfg.paths._map || cfg.paths);
    if (map && map[path]) bundle = b;
  });
  if (!bundle) return {ok:false, err:'path not in any bundle: ' + path};

  const sf = await new Promise((res, rej) => {
    bundle.load(path, cc.SpriteFrame, (e, a) => e ? rej(e) : res(a));
  }).catch(e => ({__err: String(e)}));
  if (sf && sf.__err) return {ok:false, err:'load failed: ' + sf.__err};
  if (!sf) return {ok:false, err:'null spriteFrame'};

  const tex = sf.texture || sf._texture;
  const imgAsset = tex && (tex.image || tex._image);
  let url = null;
  try {
    const u = cc.assetManager.utils;
    if (u && u.getUrlWithUuid && imgAsset && imgAsset._uuid) {
      url = u.getUrlWithUuid(imgAsset._uuid,
              {isNative:true, nativeExt:(imgAsset._native || '.png'), bundle: bundle.name});
    }
  } catch (e) { /* fall through */ }
  if (url && !/^https?:/i.test(url)) url = location.origin + '/' + url.replace(/^\//, '');
  const rect = sf.rect ? {x:sf.rect.x, y:sf.rect.y, width:sf.rect.width, height:sf.rect.height} : null;
  const rotated = !!sf.rotated;
  const diagOut = {url, rect, rotated, texW: tex && tex.width, texH: tex && tex.height};
  if (!url) return {ok:false, err:'no nativeUrl on atlas image', diag: diagOut};
  if (!rect) return {ok:false, err:'no rect', diag: diagOut};

  const img = await new Promise((res) => {
    const im = new Image();
    im.crossOrigin = 'anonymous';
    im.onload = () => res(im);
    im.onerror = () => res({__err: 'atlas image load failed: ' + url});
    im.src = url;
  });
  if (img && img.__err) return {ok:false, err: img.__err, diag: diagOut};

  try {
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (rotated) {
      canvas.width = rect.height; canvas.height = rect.width;
      ctx.translate(0, rect.width);
      ctx.rotate(-Math.PI / 2);
      ctx.drawImage(img, rect.x, rect.y, rect.height, rect.width, 0, 0, rect.height, rect.width);
    } else {
      canvas.width = rect.width; canvas.height = rect.height;
      ctx.drawImage(img, rect.x, rect.y, rect.width, rect.height, 0, 0, rect.width, rect.height);
    }
    const out = canvas.toDataURL('image/png');
    if (diag) return {ok:true, diag: diagOut, dataLen: out.length, head: out.slice(0, 48),
                      w: canvas.width, h: canvas.height};
    return {ok:true, dataURL: out, w: canvas.width, h: canvas.height};
  } catch (e) {
    return {ok:false, err:'draw failed: ' + String(e), diag: diagOut};
  }
}
"""


# Deep introspection to discover how to resolve the atlas PNG URL.
URLPROBE_JS = r"""
async (args) => {
  const {cfgId} = args;
  const path = 'ui/atlas/icon_flypet/fly_' + cfgId;
  let bundle = null;
  cc.assetManager.bundles.forEach((b) => {
    const cfg = b.config || b._config;
    const map = cfg && cfg.paths && (cfg.paths._map || cfg.paths);
    if (map && map[path]) bundle = b;
  });
  if (!bundle) return {err: 'no bundle for ' + path};
  const sf = await new Promise((res, rej) =>
    bundle.load(path, cc.SpriteFrame, (e, a) => e ? rej(e) : res(a))).catch(e => ({__err:String(e)}));
  if (sf && sf.__err) return {err: sf.__err};
  const tex = sf.texture || sf._texture;
  const imgAsset = tex && (tex.image || tex._image);
  const out = {};
  out.sfKeys = Object.keys(sf);
  out.texKeys = tex ? Object.keys(tex) : null;
  out.imgKeys = imgAsset ? Object.keys(imgAsset) : null;
  try { out.img_uuid = imgAsset && imgAsset._uuid; } catch (e) {}
  try { out.img_native = imgAsset && imgAsset._native; } catch (e) {}
  try { out.img_nativeUrl = imgAsset && imgAsset.nativeUrl; } catch (e) { out.nativeUrl_err = String(e); }
  try { out.sf_uuid = sf._uuid; } catch (e) {}
  try { out.bundleName = bundle.name; out.bundleBase = bundle.base; } catch (e) {}
  try {
    const u = cc.assetManager.utils;
    out.utilKeys = u ? Object.keys(u) : null;
    if (u && u.getUrlWithUuid && imgAsset && imgAsset._uuid) {
      out.getUrlWithUuid = u.getUrlWithUuid(imgAsset._uuid, {isNative:true, nativeExt:(imgAsset._native||'.png'), bundle: bundle.name});
    }
  } catch (e) { out.util_err = String(e); }
  try {
    out.resHits = performance.getEntriesByType('resource').map(r => r.name)
      .filter(n => /icon_flypet|\/native\/.*\.(png|webp|jpg)/i.test(n)).slice(-12);
  } catch (e) {}
  return out;
}
"""


def _game_page(browser):
    for ctx in browser.contexts:
        for p in ctx.pages:
            u = p.url or ""
            if HOST in u and "/pwa-sw" not in u:
                return p
    return None


def _write_png(config_id, data_url):
    b64 = data_url.split(",", 1)[1]
    raw = base64.b64decode(b64)
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{config_id}.png")
    with open(path, "wb") as f:
        f.write(raw)
    return path, len(raw)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        page = _game_page(browser)
        if not page:
            print(json.dumps({"ok": False, "err": "no game page on CDP " + str(PORT)}))
            return

        if mode == "urlprobe":
            cfg_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1001
            res = page.evaluate(URLPROBE_JS, {"cfgId": cfg_id})
            print(json.dumps({"urlprobe": cfg_id, "result": res}, ensure_ascii=False, indent=2, default=str))
            return

        if mode == "probe":
            cfg_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1001
            res = page.evaluate(EXTRACT_JS, {"cfgId": cfg_id, "diag": False})
            out = {"probe": cfg_id, "ok": res.get("ok"), "err": res.get("err"),
                   "diag": res.get("diag"), "w": res.get("w"), "h": res.get("h")}
            if res.get("ok") and res.get("dataURL"):
                path, nbytes = _write_png(cfg_id, res["dataURL"])
                out["written"] = path
                out["bytes"] = nbytes
            print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
            return

        if mode == "dump":
            enum = page.evaluate(ENUM_JS)
            if not enum.get("ok"):
                print(json.dumps({"ok": False, "enum": enum}, ensure_ascii=False))
                return
            ids = enum["ids"]
            written, failed = [], []
            for cid in ids:
                try:
                    res = page.evaluate(EXTRACT_JS, {"cfgId": cid, "diag": False})
                except Exception as e:  # noqa: BLE001
                    failed.append({"id": cid, "err": repr(e)})
                    continue
                if res.get("ok") and res.get("dataURL"):
                    path, nbytes = _write_png(cid, res["dataURL"])
                    written.append({"id": cid, "bytes": nbytes, "w": res.get("w"), "h": res.get("h")})
                else:
                    failed.append({"id": cid, "err": res.get("err"), "diag": res.get("diag")})
            print(json.dumps({
                "ok": True, "cache_dir": CACHE_DIR,
                "total_ids": len(ids), "written": len(written), "failed": len(failed),
                "written_detail": written, "failed_detail": failed,
            }, ensure_ascii=False, indent=2, default=str))
            return

        print(json.dumps({"ok": False, "err": "unknown mode: " + mode}))


if __name__ == "__main__":
    main()

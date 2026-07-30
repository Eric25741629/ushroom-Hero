"""從已開啟的 H5 頁面一次性匯出飛寵中文設定表。

輸出供純 WS 飛寵管理使用；執行時只讀 ``configFly`` / ``configFly_entry``，
不送遊戲封包、不點擊頁面。

用法：
  python tools/dump_flypet_catalog.py --port 9226
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "fly_pet_catalog.json"

SCRIPT = r"""
() => {
  const species = (configFly.datas || []).map(row => ({
    id: Number(row.id || 0),
    name: String(row.name || '')
  })).filter(row => row.id);
  const entries = (configFly_entry.datas || []).map(row => ({
    id: Number(row.id || 0),
    level: Number(row.level || 0),
    name: String(row.name || ''),
    quality: Number(row.quality || 0),
    desc: String(row.desc || ''),
    desc_parm: row.desc_parm || [],
    belong_talent: Number(row.belong_talent || 0),
    special_effect: row.special_effect || 0
  })).filter(row => row.id);
  return {species, entries};
}
"""

PROTOCOL_SCRIPT = r"""
async () => {
  const mod = await System.import("chunks:///_virtual/protoregister.ts");
  const out = {};
  for (const [name, id] of Object.entries(mod.MSG_TO_ID_MAP || {})) {
    if (name.startsWith('fly.')) out[name] = Number(id);
  }
  const types = {};
  for (const name of ['type.p_fly_base_pet', 'type.p_head']) {
    try { types[name] = netManager.protoRoot.lookupType(name).toJSON(); }
    catch (e) { types[name] = {error: String(e)}; }
  }
  return {commands: out, types};
}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--show-protocol", action="store_true")
    args = parser.parse_args()

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{args.port}"
        )
        game_page = next(
            (
                page
                for context in browser.contexts
                for page in context.pages
                if "mushroomh5.acenetgame.com" in page.url
                and "pwa-sw" not in page.url
            ),
            None,
        )
        if game_page is None:
            raise RuntimeError(f"port {args.port} 找不到遊戲頁面")
        catalog = game_page.evaluate(SCRIPT)
        protocol = game_page.evaluate(PROTOCOL_SCRIPT) if args.show_protocol else None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {args.output}: "
        f"{len(catalog['species'])} species, {len(catalog['entries'])} entry rows"
    )
    if protocol is not None:
        print(json.dumps(protocol, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

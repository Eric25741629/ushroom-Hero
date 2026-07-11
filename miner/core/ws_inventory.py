"""Read mining prop counts (pickaxe/drill/bomb) from WS for web_h5 devices.

The screenshot+OCR mining path reads prop counts from fixed pixel ROIs
(`check_drill_num` / `check_boom_num`). On the web_h5 browser render the bomb
ROI mis-reads 0, so the bomb gets blacklisted and the planner never plays the
drill->bomb combo (verified 2026-06-30: 5554 has bomb=930, OCR read 0).

For web_h5 we instead read the authoritative absolute counts from the WS
full-inventory snapshot (0x0401 req/resp, repeated {item_id#1, uid#2, count#3}):
pickaxe=4001, drill=4002, bomb=4003. adb devices keep using OCR.
"""
from typing import Any, Callable, Dict, Optional

from ws_token import codec

CMD_INVENTORY_QUERY = 0x0401
CMD_MINE_BOARD = 0x0C01
GOODS_PICKAXE = 4001
GOODS_DRILL = 4002
GOODS_BOMB = 4003
_ID_TO_KEY = {GOODS_PICKAXE: "pickaxe", GOODS_DRILL: "drill", GOODS_BOMB: "bomb"}


def parse_inventory_bag(raw: bytes) -> Dict[str, int]:
    """Parse a 0x0401 full-bag reply into the {pickaxe,drill,bomb} entries present.

    Only the three mining props are extracted; every other bag item is ignored.
    A present-but-zero count is kept (distinct from absent).
    """
    out: Dict[str, int] = {}
    for fnum, val in codec.walk(raw):
        if fnum == 1 and isinstance(val, (bytes, bytearray)):
            fields = codec.walk_dict(bytes(val))
            item_id = fields.get(1)
            count = fields.get(3)
            key = _ID_TO_KEY.get(item_id)
            if key is not None and isinstance(count, int):
                out[key] = count
    return out


def _web_page(d: Any) -> Optional[Any]:
    """Playwright page for a web_h5 device, else None (adb / no page)."""
    inner = getattr(d, "_d", d)
    if getattr(inner, "backend_kind", None) != "web_h5":
        return None
    return getattr(inner, "_page", None)


def read_ws_prop_counts(
    d: Any,
    *,
    timeout_sec: float = 5.0,
    _api_factory: Optional[Callable[[Any], Any]] = None,
) -> Optional[Dict[str, int]]:
    """{pickaxe,drill,bomb} from WS 0x0401 for web_h5; None if unavailable.

    None tells the caller to fall back to OCR: returned for adb backend, a
    web_h5 device whose page is not ready, a WS/RPC error, or an empty bag.
    Absent props default to 0 so a present-but-zero prop reads as 0 (not None).

    ``_api_factory`` is a seam for tests; production builds ``WebGameAPI`` lazily
    to avoid importing Playwright on the adb path.
    """
    page = _web_page(d)
    if page is None:
        return None
    factory = _api_factory
    if factory is None:
        from utils.web_game_api import WebGameAPI  # lazy: web_h5 only
        factory = WebGameAPI
    try:
        raw = factory(page).call_raw(CMD_INVENTORY_QUERY, b"", timeout_sec=timeout_sec)
    except Exception:
        return None
    counts = parse_inventory_bag(bytes(raw))
    if not counts:
        return None
    return {
        "pickaxe": counts.get("pickaxe", 0),
        "drill": counts.get("drill", 0),
        "bomb": counts.get("bomb", 0),
    }


def read_ws_below_rows(
    d: Any,
    *,
    timeout_sec: float = 5.0,
    _api_factory: Optional[Callable[[Any], Any]] = None,
) -> list:
    """視窗下方已知地形列（第 8 列起，unreachable_* 標籤）；不可用回 []。

    web_h5 限定：抓 WS 0x0c01 原始盤面，重用 ws_token 的靜態地形重建
    （``mining_adapter.build_final_v1_input``，含畫面外未採集礦坑覆寫），
    回傳其已知盤第 8 列起，供 final_v1 規劃拼在 CNN 7 列視野之後。
    adb（無 WS 可用）、頁面未就緒、RPC 失敗、地形模板缺列（visible_only）
    一律回 []，caller 維持純 7 列。
    """
    page = _web_page(d)
    if page is None:
        return []
    factory = _api_factory
    if factory is None:
        from utils.web_game_api import WebGameAPI  # lazy: web_h5 only
        factory = WebGameAPI
    try:
        raw = factory(page).call_raw(CMD_MINE_BOARD, b"", timeout_sec=timeout_sec)
        from ws_token import mining as ws_mining  # lazy: avoid adb import cost
        from ws_token import mining_adapter
        known = mining_adapter.build_final_v1_input(ws_mining.parse_board(bytes(raw)))
        return [list(row) for row in known["board"][7:]]
    except Exception:
        return []

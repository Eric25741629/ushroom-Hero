"""Safe H5/CDP executor for live mining UI calls.

This module is intentionally tiny: it only wraps known H5 JavaScript entry
points and does not connect to CDP by itself. Tests use a fake Playwright page.
"""
from __future__ import annotations

from typing import Any

from ws_token.mining import GOODS_BOMB, GOODS_DRILL, GOODS_PICKAXE


_REFRESH_BOARD_SCRIPT = """(() => {
  const dc = IS(ISInclude.MysteryDataCache);
  dc.lastReqTime = 0;
  IS(ISInclude.MysteryControl).reqHomeInfo();
  return true;
})()"""


CMD_MINE_DIG = 0x0C03
CMD_ERROR = 0x0201


class H5MiningExecutor:
    """等待伺服器動作回覆的 H5 挖礦 executor。

    舊實作只呼叫 ``reqMineUseGoods`` 就立即返回，導致伺服器的
    ``0x0201`` 拒絕看起來像成功，直到後續盤面驗證才發現。這裡改用共用
    raw-RPC helper，讓呼叫端能區分接受與明確拒絕。
    """

    def __init__(self, page: Any) -> None:
        self._page = page

    def refresh_board(self) -> Any:
        """Refresh the H5 mining board without consuming items."""
        return self._page.evaluate(_REFRESH_BOARD_SCRIPT)

    def use_pickaxe(self, block_id: int) -> Any:
        return self._use_goods(GOODS_PICKAXE, block_id)

    def use_bomb(self, block_id: int) -> Any:
        return self._use_goods(GOODS_BOMB, block_id)

    def use_drill(self, block_id: int) -> Any:
        return self._use_goods(GOODS_DRILL, block_id)

    def _use_goods(self, goods_id: int, block_id: int) -> Any:
        from utils.web_game_api import WebGameAPI, _build_dig_request_body
        from ws_token.codec import walk_dict

        try:
            response_cmd, response_body = WebGameAPI(self._page).call_raw_for(
                CMD_MINE_DIG,
                _build_dig_request_body(
                    prop_id=int(goods_id), cell_id=int(block_id)
                ),
                expect_cmds=(CMD_MINE_DIG, CMD_ERROR),
                timeout_sec=2.0,
                net_wait_ms=1000,
            )
        except Exception as exc:
            # 伺服器可能已接受動作，但 client callback 尚未被觀察到就 timeout。
            # 保留既有盤面／庫存驗證，不立即重送，避免同一道具被消耗兩次。
            return {
                "ok": True,
                "response_cmd": None,
                "response_pending": True,
                "response_error": str(exc),
            }

        raw_body = bytes(response_body)
        if response_cmd == CMD_ERROR:
            fields = walk_dict(raw_body)
            error_code = fields.get(1)
            return {
                "ok": False,
                "response_cmd": response_cmd,
                "error_code": int(error_code) if isinstance(error_code, int) else None,
                "raw_body_hex": raw_body.hex(),
            }
        return {
            "ok": True,
            "response_cmd": response_cmd,
            "raw_body_hex": raw_body.hex(),
        }

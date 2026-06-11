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


_USE_GOODS_SCRIPT = """([goodsId, blockId]) => {
  IS(ISInclude.MysteryControl).reqMineUseGoods(goodsId, blockId);
  return true;
}"""


class H5MiningExecutor:
    """Minimal guarded executor for H5 mining actions."""

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
        return self._page.evaluate(
            _USE_GOODS_SCRIPT,
            [int(goods_id), int(block_id)],
        )

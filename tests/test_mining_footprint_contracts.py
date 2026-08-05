"""v3 footprint 相容 API 必須與 core mechanics 單一來源一致。"""
from __future__ import annotations

import pytest

from miner.core.mechanics import (
    get_bomb_affected_cells,
    get_bomb_affected_cells_with_offscreen,
    get_drill_affected_cells,
)
from miner.v3.actions import get_bomb_targets, get_drill_targets


@pytest.mark.parametrize("rows, cols", [(1, 1), (3, 3), (7, 6), (10, 8)])
def test_v3_footprint_wrappers_delegate_to_core(rows: int, cols: int):
    for r in range(rows):
        for c in range(cols):
            assert get_drill_targets(r, c, rows, cols) == get_drill_affected_cells(
                r, c, rows, cols
            )
            assert get_bomb_targets(r, c, rows, cols) == get_bomb_affected_cells_with_offscreen(
                r, c, rows, cols
            )
            visible, _offscreen = get_bomb_targets(r, c, rows, cols)
            assert visible == get_bomb_affected_cells(r, c, rows, cols)


def test_bomb_offscreen_count_preserves_legacy_edge_value():
    """列在畫面外但欄同時越界的舊計數不可因去重而改變。"""
    # row 6/col 0 的 3x3 下緣含一個欄外座標；舊 actions API 仍計入它，
    # 因此 offscreen-bottom 是 4（下緣 3 + 距離 2 的下方 1）。
    assert get_bomb_targets(6, 0, 7, 6)[1] == 4

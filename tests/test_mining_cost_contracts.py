"""鎖定挖礦三種成本 API 的目前語意。

``COST_TABLE``/``enter_cost`` 是路徑進入成本，``dig_cost`` 是 v3 實際
dig 消耗，``HIT_TABLE`` 是材質擊中次數。數值看似相近也不代表可以在
沒有 replay 證據時合併成同一張表。
"""
from __future__ import annotations

import pytest

from miner.core.config import COST_TABLE, DEFAULT_CLASSES, HIT_TABLE
from miner.planning.planner import enter_cost
from miner.v3.actions import dig_cost


# 每個 DEFAULT_CLASSES 標籤的四個欄位依序是：原始進入表、planner 路徑
# API、v3 dig 消耗、擊中次數。這裡明確保留 unreachable_pit 的特例差異。
_EXPECTED = {
    "dirt": (1, 1, 1, 1),
    "dug_pit": (0, 0, 0, 0),
    "empty": (0, 0, 0, 0),
    "one_hit_rock": (1, 1, 1, 1),
    "reachable_pit": (1, 1, 1, 1),
    "rock": (2, 2, 2, 2),
    "unreachable_dirt": (1, 1, 1, 1),
    "unreachable_pit": (None, 1, 1, 1),
    "unreachable_rock": (2, 2, 2, 2),
    "unreachable_empty": (0, 0, 0, 0),
}


@pytest.mark.parametrize("label", DEFAULT_CLASSES)
def test_default_class_cost_semantics_are_explicit(label: str):
    table_cost, path_cost, action_cost, hit_count = _EXPECTED[label]

    assert COST_TABLE[label] == table_cost
    assert enter_cost(label) == path_cost
    assert dig_cost(label) == action_cost
    assert HIT_TABLE[label] == hit_count


def test_unreachable_pit_difference_is_intentional_contract():
    """原始表仍標記不可直接進入，但既有 planner/action API 可處理它。"""
    assert COST_TABLE["unreachable_pit"] is None
    assert enter_cost("unreachable_pit") == COST_TABLE["reachable_pit"]
    assert dig_cost("unreachable_pit") == 1


@pytest.mark.parametrize(
    ("label", "expected"),
    [("pit", 0), ("void", 0), ("unreachable_void", None)],
)
def test_legacy_aliases_keep_planner_entry_semantics(label: str, expected):
    assert enter_cost(label) == expected

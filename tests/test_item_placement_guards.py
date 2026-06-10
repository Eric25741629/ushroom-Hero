"""Tests protecting against placing items on unreachable cells."""

import pytest

from miner.planning.executor import is_placeable_label

def test_executor_placeable_labels_follow_base_label_contract() -> None:
    # 現行契約（executor docstring）：unreachable_empty 以 base_label 視同 empty，
    # 與 planner 一致；只有 empty/dug_pit 材質可放道具。
    assert is_placeable_label("empty")
    assert is_placeable_label("dug_pit")
    assert is_placeable_label("unreachable_empty")
    assert not is_placeable_label("unreachable_void")
    assert not is_placeable_label("pit")
    assert not is_placeable_label("void")


def test_item_planner_requires_actual_empty_cells() -> None:
    # legacy 腳本用 flat import（simplecnn）+ u2/torch，環境不齊時只跳過本測試
    mining_legacy = pytest.importorskip(
        "miner.scripts.Mining_等待改進",
        reason="legacy mining script not importable in this environment",
    )
    assert mining_legacy._can_place_item("empty")  # type: ignore[attr-defined]
    assert mining_legacy._can_place_item("dug_pit")  # type: ignore[attr-defined]
    assert not mining_legacy._can_place_item("unreachable_empty")  # type: ignore[attr-defined]
    assert not mining_legacy._can_place_item("pit")  # type: ignore[attr-defined]

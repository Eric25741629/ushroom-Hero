"""Tests protecting against placing items on unreachable cells."""

from miner.executor import is_placeable_label
from miner import Mining_等待改進 as mining_legacy


def test_executor_disallows_unreachable_labels() -> None:
    assert is_placeable_label("empty")
    assert is_placeable_label("dug_pit")
    assert not is_placeable_label("unreachable_empty")
    assert not is_placeable_label("unreachable_void")


def test_item_planner_requires_actual_empty_cells() -> None:
    assert mining_legacy._can_place_item("empty")  # type: ignore[attr-defined]
    assert mining_legacy._can_place_item("dug_pit")  # type: ignore[attr-defined]
    assert not mining_legacy._can_place_item("unreachable_empty")  # type: ignore[attr-defined]
    assert not mining_legacy._can_place_item("pit")  # type: ignore[attr-defined]

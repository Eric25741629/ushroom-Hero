"""WS runner 與 daily pipeline 交接清單的特徵化安全網。

這組測試只讀 production source，不啟動 ADB、Playwright 或 WS client。
`WS_TO_PIPELINE_SKIPS` 同時包含三種既有語意：registry direct skip、gacha
的特殊 `ctx.ws_done` 判斷，以及 H5-only/條件式任務；測試因此分層核對，
避免把合法的 backend fallback 誤判成清單錯誤。
"""
from __future__ import annotations

import ast
from pathlib import Path

from game_actions.task_registry import (
    get_task_definition,
    pipeline_display_names,
    ws_task_ids,
)
from game_actions import ws_phase as ws_phase_runtime


ROOT = Path(__file__).resolve().parents[1]

# 中文 task label 改由 registry 產生；新增/改名時不再維護第二張手抄清單。
PIPELINE_TASK_NAMES = frozenset(pipeline_display_names())

# `萬神試煉` 是既有條件式 skip：只有配置 dungeon_sweeps 時才由
# ws_phase.run_ws_phase 額外加入，不會出現在無條件對照表。
CONDITIONAL_PIPELINE_SKIPS = frozenset({"萬神試煉"})


def _source(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _literal_assignment(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(value)
    raise AssertionError(f"找不到靜態設定 {name}")


def test_runner_task_order_is_a_live_registry_projection():
    runner = _source("ws_token/runner.py")
    mapping = ws_phase_runtime.WS_TO_PIPELINE_SKIPS
    task_order = next(
        node
        for node in ast.walk(runner)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "TASK_ORDER"
    )

    assert isinstance(task_order.value, ast.Call)
    assert isinstance(task_order.value.func, ast.Name)
    assert task_order.value.func.id == "ws_task_ids"
    assert len(set(ws_task_ids())) == len(ws_task_ids())
    assert set(mapping).issubset(set(ws_task_ids())), sorted(set(mapping) - set(ws_task_ids()))


def test_ws_mapping_values_are_known_pipeline_task_labels():
    mapping = ws_phase_runtime.WS_TO_PIPELINE_SKIPS
    mapped_names = {name for names in mapping.values() for name in names}

    assert mapped_names <= PIPELINE_TASK_NAMES
    assert mapped_names, "WS 對照表不可退化成空表"


def _ws_done_membership_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            continue
        if not any(
            isinstance(child, ast.Attribute) and child.attr == "ws_done"
            for child in ast.walk(node)
        ):
            continue
        names.update(
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        )
    return names


def test_special_gacha_partial_skip_remains_explicitly_covered():
    pipeline = _source("game_actions/daily_pipeline.py")
    # gacha intentionally skips only the paid weekend draw, so its direct
    # membership check remains separately pinned.
    special_gacha = "抽技能夥伴"
    definition = get_task_definition("gacha")

    assert "partial-client-skip" in definition.tags
    assert special_gacha in definition.skip_when_ws_done
    assert special_gacha in _ws_done_membership_names(pipeline)
    assert any(
        isinstance(node, ast.Constant) and node.value == "抽技能夥伴"
        for node in ast.walk(pipeline)
    )


def test_daily_record_keys_are_mapped_or_explicitly_conditional():
    ws_phase = _source("game_actions/ws_phase.py")
    mapping = ws_phase_runtime.WS_TO_PIPELINE_SKIPS
    record_map = ws_phase_runtime.SKIP_TO_DAILY_RECORD
    mapped_names = {name for names in mapping.values() for name in names}

    assert set(record_map) <= mapped_names | CONDITIONAL_PIPELINE_SKIPS
    assert set(record_map), "daily ledger 對照表不可退化成空表"


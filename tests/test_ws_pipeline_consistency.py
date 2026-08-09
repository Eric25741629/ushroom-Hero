"""WS runner 與 daily pipeline 交接清單的特徵化安全網。

這組測試只讀 production source，不啟動 ADB、Playwright 或 WS client。
`WS_TO_PIPELINE_SKIPS` 同時包含三種既有語意：直接 `_ws_skip()`、gacha
的特殊 `ctx.ws_done` 判斷，以及 H5-only/條件式任務；測試因此分層核對，
避免把合法的 backend fallback 誤判成清單錯誤。
"""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# 這是 daily_pipeline 現行公開給交接層的中文 task label；改名時必須同時
# 更新 WS_TO_PIPELINE_SKIPS，否則 WS 成功後會靜默漏跑或重跑 client 任務。
PIPELINE_TASK_NAMES = frozenset({
    "坐騎強化",
    "紅包檢查",
    "點擊寶箱",
    "家族任務",
    "領取守護靈",
    "商店購買",
    "所有日常任務",
    "好友每日禮物",
    "開神燈",
    "轉盤金幣",
    "挖礦/Oracle",
    "抽技能夥伴",
    "航海任務 (Sea)",
    "競技場挑戰",
    "天梯每週獎勵",
    "七日登入獎勵",
    "雲端戰鬥",
    "菇菇武道會",
    "地獄之門",
    "賞金之路",
    "農場任務",
    "萬神試煉",
})

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


def _pipeline_skip_calls(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "_ws_skip":
            if len(node.args) == 1 and isinstance(node.args[0], ast.Constant):
                names.add(node.args[0].value)
    return names


def _task_name_keywords(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in {"task", "task_name", "display_name"}:
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    names.add(keyword.value.value)
    return names


def test_ws_task_keys_are_a_subset_of_the_40_task_order_entries():
    runner = _literal_assignment(_source("ws_token/runner.py"), "TASK_ORDER")
    mapping = _literal_assignment(
        _source("game_actions/ws_phase.py"), "WS_TO_PIPELINE_SKIPS"
    )

    assert len(runner) == 40
    assert len(set(runner)) == len(runner)
    assert set(mapping).issubset(set(runner)), sorted(set(mapping) - set(runner))


def test_ws_mapping_values_are_known_pipeline_task_labels():
    mapping = _literal_assignment(
        _source("game_actions/ws_phase.py"), "WS_TO_PIPELINE_SKIPS"
    )
    mapped_names = {name for names in mapping.values() for name in names}

    assert mapped_names <= PIPELINE_TASK_NAMES
    assert mapped_names, "WS 對照表不可退化成空表"


def test_direct_ws_skip_hooks_and_special_gacha_hook_are_covered():
    pipeline = _source("game_actions/daily_pipeline.py")
    mapping = _literal_assignment(
        _source("game_actions/ws_phase.py"), "WS_TO_PIPELINE_SKIPS"
    )
    mapped_names = {name for names in mapping.values() for name in names}

    direct_hooks = _pipeline_skip_calls(pipeline)
    # gacha intentionally skips only the paid weekend draw, so it uses a direct
    # membership check instead of `_ws_skip()` and must remain separately pinned.
    special_gacha = "抽技能夥伴"

    assert direct_hooks <= mapped_names | {"農場任務", "點擊寶箱", "萬神試煉"}
    assert special_gacha in mapped_names
    assert "抽技能夥伴" in _task_name_keywords(pipeline)


def test_daily_record_keys_are_mapped_or_explicitly_conditional():
    ws_phase = _source("game_actions/ws_phase.py")
    mapping = _literal_assignment(ws_phase, "WS_TO_PIPELINE_SKIPS")
    record_map = _literal_assignment(ws_phase, "SKIP_TO_DAILY_RECORD")
    mapped_names = {name for names in mapping.values() for name in names}

    assert set(record_map) <= mapped_names | CONDITIONAL_PIPELINE_SKIPS
    assert set(record_map), "daily ledger 對照表不可退化成空表"


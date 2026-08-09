"""W8 lamp executor 的 characterization / contract tests。

測試只檢查 registry metadata、薄 adapter 的轉接，以及 Task 18 → Task 19
的既有 ``stage`` 契約；不載入真實 cv2、Playwright、ADB 或 WS client。
"""
from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

from game_actions.task_registry import (
    get_task_definition,
    ws_task_ids,
    ws_to_pipeline_skip_mapping,
)


ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _run_tasks_source() -> ast.FunctionDef:
    tree = _source("game_actions/daily_pipeline.py")
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_tasks"
    )


def _call_lines(function: ast.FunctionDef, function_name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == function_name)
            or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == function_name
            )
        )
    ]


def test_lamp_registry_expresses_ws_client_skip_and_batch_contract():
    definition = get_task_definition("lamp")

    assert definition.executors == {
        "ws": "ws_token.runner:run_device",
        "adb": "game_actions.executors.lamp_executor:run_client",
        "web_h5": "game_actions.executors.lamp_executor:run_client",
    }
    assert definition.skip_when_ws_done == ("開神燈",)
    assert ws_to_pipeline_skip_mapping()["lamp"] == ("開神燈",)
    assert definition.batch_cap == 20
    assert "lamp" in ws_task_ids()

    runner = _source("ws_token/runner.py")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ws_task_ids"
        for node in ast.walk(runner)
    ), "WS live path 必須讀取 registry projection"


def test_client_executor_is_a_thin_adapter_to_existing_lamp_scheduler(monkeypatch):
    calls: list[tuple[object, str, str]] = []
    sentinel = object()
    fake_scheduler = types.ModuleType("game_actions.lamp_scheduler")

    def fake_run_lamp_if_due(device, ip, stage):
        calls.append((device, ip, stage))
        return sentinel

    fake_scheduler._run_lamp_if_due = fake_run_lamp_if_due
    monkeypatch.setitem(sys.modules, "game_actions.lamp_scheduler", fake_scheduler)

    executor = importlib.import_module("game_actions.executors.lamp_executor")
    device = object()

    assert executor.run_client(device, "lamp-device", "主頁面") is sentinel
    assert calls == [(device, "lamp-device", "主頁面")]


def test_lamp_batch_cap_matches_the_existing_ws_api_limit_without_importing_runner():
    runner = _source("ws_token/runner.py")
    batch_value = next(
        node.value.value
        for node in ast.walk(runner)
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "_LAMP_BATCH_NUM"
        and isinstance(node.value, ast.Constant)
    )

    assert get_task_definition("lamp").batch_cap == batch_value == 20


def test_task_18_refreshes_stage_before_task_19_lamp_call():
    run_tasks = _run_tasks_source()
    friend_gift_line = min(
        _call_lines(run_tasks, "buy_gift_for_friend_daily")
    )
    refresh_lines = [
        node.lineno
        for node in ast.walk(run_tasks)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "stage"
            for target in node.targets
        )
        and any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "get_stage_with_check"
            for child in ast.walk(node.value)
        )
    ]
    lamp_calls = [
        node
        for node in ast.walk(run_tasks)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_lamp_if_due"
    ]

    assert lamp_calls, "Task 19 必須仍呼叫既有 lamp scheduler"
    lamp_call = lamp_calls[0]
    assert any(
        isinstance(argument, ast.Name) and argument.id == "stage"
        for argument in lamp_call.args
    )
    assert any(friend_gift_line < line < lamp_call.lineno for line in refresh_lines)

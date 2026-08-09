"""任務註冊表的資料模型、既有對照與輕量匯入契約。"""
from __future__ import annotations

import ast
import dataclasses
import datetime
import re
from pathlib import Path

import pytest

from game_actions.task_registry import (
    DuePolicy,
    TaskDefinition,
    TaskOutcome,
    TaskResult,
    get_task_definition,
    iter_pipeline_task_definitions,
    iter_task_definitions,
    pipeline_display_names,
    task_ids,
    ws_task_ids,
    ws_to_pipeline_skip_mapping,
)


ROOT = Path(__file__).resolve().parents[1]


def _literal_assignment(relative_path: str, name: str):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
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


def _runner_execution_order() -> tuple[str, ...]:
    """從 run_device 實際 call sites 取順序，不信任 TASK_ORDER/registry 自述。

    正常任務都經 `_step("id", ...)`；`main_chapter_kills` 因必須在主 WS
    關閉後執行，刻意走尾端獨立 `_safe(..., name, ...)`，故另驗證再接到尾端。
    """
    path = ROOT / "ws_token/runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    run_device = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "run_device"
    )
    stepped = sorted(
        (node.lineno, node.args[0].value)
        for node in ast.walk(run_device)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_step"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        )
    )
    tail_assignment = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "name" for target in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value == "main_chapter_kills"
        for node in ast.walk(run_device)
    )
    tail_safe_call = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_safe"
        and any(isinstance(arg, ast.Name) and arg.id == "name" for arg in node.args)
        for node in ast.walk(run_device)
    )

    assert len(stepped) == 40
    assert tail_assignment and tail_safe_call
    return tuple(name for _line, name in stepped) + ("main_chapter_kills",)


def test_registry_is_the_union_of_client_and_actual_ws_tasks():
    definitions = iter_task_definitions()

    assert len(definitions) == 44
    assert len(task_ids()) == len(set(task_ids())) == 44
    assert ws_task_ids() == _runner_execution_order()
    assert len(ws_task_ids()) == 41
    assert {"daily_acceleration", "fannaoxiao", "biweekly"} <= set(task_ids())
    assert "xwar_idle" in ws_task_ids()
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", task_id) for task_id in task_ids())


def test_xwar_idle_keeps_the_production_label_and_config_gate():
    definition = get_task_definition("xwar_idle")

    assert definition.display_name == "跨服戰放置獎勵"
    assert definition.enabled_key == "xwar_idle"


def test_task_definition_stays_within_the_review_field_budget():
    fields = dataclasses.fields(TaskDefinition)

    assert len(fields) <= 18
    assert {field.name for field in fields} == {
        "task_id", "display_name", "order", "enabled_key", "due_policy",
        "executors", "completion_policy", "skip_when_ws_done",
        "needs_main_page", "record_name", "timeout_sec", "retry_policy",
        "time_window", "device_excludes", "batch_cap", "tags",
        "ws_display_name",
    }


def test_task_outcome_and_interruption_result_have_explicit_semantics():
    assert {outcome.value for outcome in TaskOutcome} == {
        "completed", "skipped", "retryable_failure", "permanent_failure",
        "interrupted",
    }
    interrupted = TaskResult(TaskOutcome.INTERRUPTED, detail="使用者強制休眠")

    assert interrupted.retry_after_sec is None
    assert dict(interrupted.completion_updates) == {}
    with pytest.raises(ValueError, match="INTERRUPTED"):
        TaskResult(
            TaskOutcome.INTERRUPTED,
            completion_updates={"should_not_be_written": True},
        )


def test_task_result_retry_metadata_is_limited_to_retryable_failures():
    retryable = TaskResult(
        TaskOutcome.RETRYABLE_FAILURE,
        detail="暫時逾時",
        retry_after_sec=30.0,
    )

    assert retryable.retry_after_sec == 30.0
    with pytest.raises(ValueError, match="RETRYABLE_FAILURE"):
        TaskResult(TaskOutcome.COMPLETED, retry_after_sec=1.0)


def test_task_result_supports_typed_structured_completion_updates():
    result = TaskResult(
        TaskOutcome.COMPLETED,
        completion_updates={"farm_plant_click": {"count": 1}},
    )
    nested = result.completion_updates["farm_plant_click"]

    assert dict(nested) == {"count": 1}
    with pytest.raises(TypeError):
        nested["count"] = 2  # type: ignore[index]


def test_due_policy_delegates_to_the_existing_task_due_predicate(monkeypatch):
    from game_actions import task_due

    seen: list[tuple[str, datetime.datetime]] = []
    now = datetime.datetime(2026, 8, 9, 12, 34, tzinfo=datetime.timezone.utc)

    def predicate(ip: str, resolved: datetime.datetime) -> bool:
        seen.append((ip, resolved))
        return True

    monkeypatch.setitem(task_due._REGISTRY, "測試 due", predicate)

    assert DuePolicy("測試 due").is_due("registry-device", now) is True
    assert seen == [("registry-device", now)]


def test_registry_keeps_existing_ws_skip_mapping_and_conditional_labels():
    from game_actions import ws_phase

    existing = ws_phase.WS_TO_PIPELINE_SKIPS

    assert ws_to_pipeline_skip_mapping() == existing
    assert ws_to_pipeline_skip_mapping(include_conditional=True) | existing == {
        **existing,
        "farm": ("農場任務",),
        "dungeon": ("萬神試煉",),
    }
    assert set(pipeline_display_names()) >= {
        name
        for names in ws_to_pipeline_skip_mapping(include_conditional=True).values()
        for name in names
    }


def test_registry_keeps_existing_dashboard_display_names():
    existing = _literal_assignment("game_actions/ws_phase.py", "_WS_TASK_LABELS")

    assert {
        task_id: (
            get_task_definition(task_id).ws_display_name
            or get_task_definition(task_id).display_name
        )
        for task_id in existing
    } == existing


def test_completion_policies_preserve_the_three_existing_ledger_schemas():
    assert get_task_definition("main_tasks").completion_policy.schema == "flat_scalar"
    assert get_task_definition("main_tasks").completion_policy.record_keys == (
        "mission_timestamp",
    )
    assert get_task_definition("ad_rewards").completion_policy.schema == "timestamp_record"
    assert get_task_definition("ad_rewards").completion_policy.record_keys == (
        "farm_plant_click",
    )
    assert get_task_definition("farm").completion_policy.schema == "record_time"
    assert get_task_definition("farm").completion_policy.record_keys == (
        "farm_seed_purchase",
    )
    assert get_task_definition("steward").completion_policy.schema == "daily_record"
    assert get_task_definition("steward").completion_policy.record_keys == ("Store",)


def test_pipeline_projection_matches_w5_independent_order_oracle():
    expected = _literal_assignment("tests/test_daily_pipeline_order.py", "EXPECTED_ORDER")
    pipeline = iter_pipeline_task_definitions()

    assert len(pipeline) == 28
    assert tuple(item.display_name for item in pipeline) == tuple(expected)
    assert pipeline_display_names() == tuple(expected)
    assert tuple(item.order for item in pipeline) == tuple(range(10, 281, 10))
    assert all("adb" in item.executors or "web_h5" in item.executors for item in pipeline)


def test_ws_only_tasks_use_zero_order_and_client_tasks_use_positive_order():
    for definition in iter_task_definitions():
        has_client = "adb" in definition.executors or "web_h5" in definition.executors
        assert (definition.order > 0) is has_client


def test_order_sentinel_rejects_mismatched_executor_scope():
    with pytest.raises(ValueError, match="client task"):
        TaskDefinition(
            "bad_client_order",
            "錯誤 client 順序",
            0,
            executors={"adb": "game_actions.daily_pipeline:run"},
        )
    with pytest.raises(ValueError, match="WS-only"):
        TaskDefinition(
            "bad_ws_order",
            "錯誤 WS 順序",
            10,
            executors={"ws": "ws_token.runner:run_device"},
        )


def test_executor_references_are_resolvable_shared_and_specialized_entrypoints():
    symbol_cache: dict[str, set[str]] = {}
    references = {
        reference
        for definition in iter_task_definitions()
        for reference in definition.executors.values()
    }

    assert references == {
        "game_actions.daily_pipeline:run",
        "ws_token.runner:run_device",
        "game_actions.executors.lamp_executor:run_client",
        "game_actions.executors.farm_executor:run_client",
        "game_actions.executors.farm_executor:run_daily_client",
    }

    for definition in iter_task_definitions():
        for reference in definition.executors.values():
            module_name, symbol = reference.split(":", 1)
            if module_name not in symbol_cache:
                tree = _source_module(module_name.replace(".", "/") + ".py")
                symbol_cache[module_name] = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            assert symbol in symbol_cache[module_name], reference


def _source_module(relative_path: str) -> ast.Module:
    path = ROOT / relative_path
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def test_registry_read_api_is_immutable_and_human_readable():
    definitions = iter_task_definitions()

    assert isinstance(definitions, tuple)
    assert tuple(item.task_id for item in definitions) == task_ids()
    assert all(re.search(r"[\u4e00-\u9fff]", item.display_name) for item in definitions)
    with pytest.raises(TypeError):
        definitions[0].executors["adb"] = "不可修改"  # type: ignore[index]
    with pytest.raises(KeyError, match="unknown task"):
        get_task_definition("not_registered")


def test_registry_module_has_no_runtime_import_or_lambda_table():
    path = ROOT / "game_actions/task_registry.py"
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(
        name.startswith(("cv2", "playwright", "ws_token.runner", "game_actions.ws_phase"))
        for name in imported
    )
    assert not any(isinstance(node, ast.Lambda) for node in ast.walk(tree))
    assert "Any" not in source

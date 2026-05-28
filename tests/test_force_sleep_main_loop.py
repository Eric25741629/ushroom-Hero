from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _is_dashboard_force_sleep_raise(node: ast.AST) -> bool:
    if not isinstance(node, ast.Raise):
        return False
    exc = node.exc
    if not isinstance(exc, ast.Call) or _name(exc.func) != "ForceSleepRequested":
        return False
    return bool(
        exc.args
        and isinstance(exc.args[0], ast.Constant)
        and exc.args[0].value == "force sleep requested from dashboard"
    )


def _contains_dashboard_force_sleep_raise(node: ast.AST) -> bool:
    return any(_is_dashboard_force_sleep_raise(child) for child in ast.walk(node))


def _handles_force_sleep(try_node: ast.Try) -> bool:
    return any(_name(handler.type) == "ForceSleepRequested" for handler in try_node.handlers)


def _main_function() -> ast.FunctionDef:
    source = (ROOT / "new_main_v2.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("main() not found")


def test_dashboard_force_sleep_checkpoint_is_handled_as_normal_sleep():
    main_fn = _main_function()
    handled_tries = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Try)
        and _handles_force_sleep(node)
        and any(_contains_dashboard_force_sleep_raise(stmt) for stmt in node.body)
    ]
    assert handled_tries, "dashboard force-sleep raise must be inside the ForceSleepRequested handler scope"


def test_force_sleep_now_resets_each_main_loop_iteration():
    main_fn = _main_function()
    loops = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.While) and _contains_dashboard_force_sleep_raise(node)
    ]
    assert loops, "main device loop not found"
    loop = loops[0]

    resets_in_loop_body = [
        stmt
        for stmt in loop.body
        if isinstance(stmt, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "force_sleep_now" for target in stmt.targets)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is False
    ]
    assert resets_in_loop_body, "force_sleep_now must not leak across wake/sleep iterations"

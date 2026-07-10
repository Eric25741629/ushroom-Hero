# WS to H5 Online-Check Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip the redundant online-presence check when a web_h5 device has just completed its own WS phase cleanly, while retaining the check after login failure, kick/drop, abort, or unexpected failure.

**Architecture:** Add a per-device, thread-safe WS-to-H5 handoff flag beside the existing `ws_login_ok` state. `run_ws_phase()` resets it at the start and sets it only after a complete, non-kicked, non-aborted run; a lightweight resolver in `runtime_services.web_session_service` combines that flag with the legacy initialization skip, and `new_main_v2` passes the resolved value into the existing wake handler.

**Tech Stack:** Python 3.10, pytest, standard-library threading, AST-based wiring tests, Conda environment `mushroom1`.

---

## File Map

- Modify `bot_state.py`: own the thread-safe per-device `ws_h5_handoff_ok` state.
- Modify `game_actions/ws_phase.py`: reset and publish the health of the current WS-to-H5 handoff.
- Modify `runtime_services/web_session_service.py`: resolve whether this wake may skip online checking without importing the heavy main module.
- Modify `new_main_v2.py`: use the resolver immediately before `handle_device_wakeup()`.
- Create `tests/test_bot_state_ws_h5_handoff.py`: verify state defaults and per-device isolation.
- Modify `tests/test_ws_phase.py`: verify all healthy and interrupted WS outcomes.
- Modify `tests/test_checker_gate_web_launch.py`: verify resolver behavior.
- Modify `tests/test_h5_ws_startup_order.py`: verify main-loop wiring through AST.

### Task 1: Add Thread-Safe Handoff State

**Files:**
- Modify: `bot_state.py:57-60`
- Modify: `bot_state.py:762-778`
- Create: `tests/test_bot_state_ws_h5_handoff.py`

- [ ] **Step 1: Write the failing state tests**

Create `tests/test_bot_state_ws_h5_handoff.py`:

```python
"""Per-device WS -> H5 handoff signal stored by bot_state."""
import bot_state


def _clear(ip: str) -> None:
    with bot_state._global_lock:
        bot_state._ws_h5_handoff_ok.pop(ip, None)


def test_ws_h5_handoff_defaults_false_and_round_trips():
    ip = "handoff-round-trip"
    _clear(ip)
    try:
        assert bot_state.get_ws_h5_handoff_ok(ip) is False
        bot_state.set_ws_h5_handoff_ok(ip, True)
        assert bot_state.get_ws_h5_handoff_ok(ip) is True
        bot_state.set_ws_h5_handoff_ok(ip, False)
        assert bot_state.get_ws_h5_handoff_ok(ip) is False
    finally:
        _clear(ip)


def test_ws_h5_handoff_is_isolated_per_device():
    first = "handoff-first"
    second = "handoff-second"
    _clear(first)
    _clear(second)
    try:
        bot_state.set_ws_h5_handoff_ok(first, True)
        assert bot_state.get_ws_h5_handoff_ok(first) is True
        assert bot_state.get_ws_h5_handoff_ok(second) is False
    finally:
        _clear(first)
        _clear(second)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_bot_state_ws_h5_handoff.py -q
```

Expected: collection or execution fails because `_ws_h5_handoff_ok`, `set_ws_h5_handoff_ok`, and `get_ws_h5_handoff_ok` do not exist.

- [ ] **Step 3: Implement the minimal state API**

In `bot_state.py`, add the storage beside `_ws_login_ok`:

```python
# 本輪 WS 是否健康完成，可安全直接交接 H5；與「曾登入成功」分開記錄。
_ws_h5_handoff_ok: Dict[str, bool] = {}
```

Add the API beside `set_ws_login_ok()` / `get_ws_login_ok()`:

```python
def set_ws_h5_handoff_ok(ip: str, ok: bool) -> None:
    """記錄本輪 WS 是否可安全直接交接 H5。"""
    with _global_lock:
        _ws_h5_handoff_ok[ip] = bool(ok)


def get_ws_h5_handoff_ok(ip: str) -> bool:
    """回傳本輪 WS 到 H5 的健康交接狀態；未設定時採 fail-closed。"""
    with _global_lock:
        return bool(_ws_h5_handoff_ok.get(ip, False))
```

- [ ] **Step 4: Run the state tests and verify GREEN**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_bot_state_ws_h5_handoff.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit the state API**

```powershell
git add bot_state.py tests/test_bot_state_ws_h5_handoff.py
git commit -m "feat(runtime): track WS to H5 handoff health"
```

### Task 2: Publish Healthy and Interrupted WS Outcomes

**Files:**
- Modify: `game_actions/ws_phase.py:560-570`
- Modify: `game_actions/ws_phase.py:685-732`
- Modify: `tests/test_ws_phase.py:26-29`
- Modify: `tests/test_ws_phase.py:220-245`

- [ ] **Step 1: Extend the RunReport test helper**

Change the helper in `tests/test_ws_phase.py` so tests can express transport interruption:

```python
def _report(tasks, errors=None, login_ok=True, *, kicked=False, aborted=False):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {}, kicked=kicked,
                     aborted=aborted)
```

- [ ] **Step 2: Write failing handoff lifecycle tests**

Add these tests near the existing login/exception cases in `tests/test_ws_phase.py`:

```python
def test_clean_ws_run_marks_h5_handoff_safe(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(
        ws_phase, "_run_device",
        lambda ip, cfg, progress=None, **_kw: _report({"redpack": {}}),
    )
    ws_phase.run_ws_phase("dev")
    import bot_state
    assert bot_state.get_ws_h5_handoff_ok("dev") is True


def test_task_error_still_marks_h5_handoff_safe(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(
        ws_phase, "_run_device",
        lambda ip, cfg, progress=None, **_kw: _report(
            {"redpack": {}}, errors={"lamp": "WSTimeoutError: x"}
        ),
    )
    ws_phase.run_ws_phase("dev")
    import bot_state
    assert bot_state.get_ws_h5_handoff_ok("dev") is True


@pytest.mark.parametrize(
    "report",
    [
        _report({}, errors={"login": "expired"}, login_ok=False),
        _report({}, kicked=True),
        _report({}, aborted=True),
    ],
    ids=["login-failed", "kicked", "aborted"],
)
def test_interrupted_ws_run_keeps_h5_handoff_unsafe(monkeypatch, report):
    _cfg(monkeypatch, {"enabled": True})
    import bot_state
    bot_state.set_ws_h5_handoff_ok("dev", True)
    monkeypatch.setattr(
        ws_phase, "_run_device",
        lambda ip, cfg, progress=None, **_kw: report,
    )
    ws_phase.run_ws_phase("dev")
    assert bot_state.get_ws_h5_handoff_ok("dev") is False


def test_ws_exception_resets_previous_h5_handoff(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    import bot_state
    bot_state.set_ws_h5_handoff_ok("dev", True)

    def boom(ip, cfg, progress=None, **_kw):
        raise RuntimeError("transport failed")

    monkeypatch.setattr(ws_phase, "_run_device", boom)
    ws_phase.run_ws_phase("dev")
    assert bot_state.get_ws_h5_handoff_ok("dev") is False
```

Also add `import pytest` near the top of `tests/test_ws_phase.py` if it is not already imported.

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_ws_phase.py -q
```

Expected: the new clean/task-error assertions fail because no handoff value is published; interrupted cases may incorrectly retain the seeded prior value.

- [ ] **Step 4: Reset the handoff flag at the start of every enabled WS attempt**

In `game_actions/ws_phase.py`, extend the existing best-effort reset block:

```python
    try:
        import bot_state
        bot_state.set_ws_login_ok(ip, False)
        bot_state.set_ws_h5_handoff_ok(ip, False)
    except Exception:  # noqa: BLE001
        log.debug("[%s] 重置 WS 登入/交接訊號失敗（忽略）", ip, exc_info=True)
```

- [ ] **Step 5: Publish success only after the phase finishes cleanly**

Immediately before the final `return frozenset(skips)` in `run_ws_phase()`, add:

```python
    if not report.kicked and not report.aborted:
        try:
            import bot_state
            bot_state.set_ws_h5_handoff_ok(ip, True)
        except Exception:  # noqa: BLE001
            log.debug("[%s] 設定 WS→H5 健康交接訊號失敗（忽略）", ip, exc_info=True)
```

Do not gate on `report.errors`: ordinary task errors intentionally retain a healthy handoff.

- [ ] **Step 6: Run WS phase tests and verify GREEN**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_ws_phase.py tests/test_bot_state_ws_h5_handoff.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the WS lifecycle behavior**

```powershell
git add game_actions/ws_phase.py tests/test_ws_phase.py
git commit -m "fix(ws): publish safe H5 handoff only after healthy run"
```

### Task 3: Resolve and Wire the Online-Check Skip

**Files:**
- Modify: `runtime_services/web_session_service.py:140-175`
- Modify: `new_main_v2.py:55-66`
- Modify: `new_main_v2.py:358-372`
- Modify: `tests/test_checker_gate_web_launch.py`
- Modify: `tests/test_h5_ws_startup_order.py`

- [ ] **Step 1: Write failing resolver tests**

Append to `tests/test_checker_gate_web_launch.py`:

```python
@pytest.mark.parametrize(
    ("backend", "ws_enabled", "initial_skip", "handoff_ok", "expected"),
    [
        ("web_h5", True, False, True, True),
        ("web_h5", True, True, False, False),
        ("web_h5", False, True, False, True),
        ("web_h5", False, False, True, False),
        ("adb", True, True, False, True),
    ],
)
def test_resolve_skip_online_check_once(
    monkeypatch, backend, ws_enabled, initial_skip, handoff_ok, expected
):
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda _ip: {"ws_token": {"enabled": ws_enabled}},
    )
    monkeypatch.setattr(
        svc.bot_state,
        "get_ws_h5_handoff_ok",
        lambda _ip: handoff_ok,
    )
    assert svc.resolve_skip_online_check_once(
        "dev", backend, initial_skip=initial_skip
    ) is expected


def test_resolve_skip_online_check_once_fails_closed_on_config_error(monkeypatch):
    monkeypatch.setattr(
        svc.config_manager,
        "get_device_config",
        lambda _ip: (_ for _ in ()).throw(RuntimeError("bad config")),
    )
    assert svc.resolve_skip_online_check_once(
        "dev", "web_h5", initial_skip=True
    ) is False
```

- [ ] **Step 2: Write the failing main wiring test**

Append to `tests/test_h5_ws_startup_order.py`:

```python
def test_main_resolves_online_skip_after_ws_before_wakeup():
    main_fn = _main_function()
    resolver_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and _name(node.func) == "resolve_skip_online_check_once"
    ]
    assert resolver_calls, "main() must resolve the current WS-to-H5 handoff"

    wake_calls = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call) and _name(node.func) == "handle_device_wakeup"
    ]
    skip_values = [
        keyword.value
        for call in wake_calls
        for keyword in call.keywords
        if keyword.arg == "skip_online_check_once"
    ]
    assert any(
        isinstance(value, ast.Name) and value.id == "skip_online_check_for_wakeup"
        for value in skip_values
    ), "handle_device_wakeup() must receive the resolved current-cycle value"
```

- [ ] **Step 3: Run resolver and wiring tests and verify RED**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_checker_gate_web_launch.py tests/test_h5_ws_startup_order.py -q
```

Expected: tests fail because `resolve_skip_online_check_once()` and the main-loop call do not exist.

- [ ] **Step 4: Implement the fail-closed resolver**

Add to `runtime_services/web_session_service.py` before `initialize_runtime_device()`:

```python
def resolve_skip_online_check_once(
    ip: str,
    backend_kind: str,
    *,
    initial_skip: bool = False,
) -> bool:
    """決定本輪喚醒是否可略過在線互檢。"""
    if str(backend_kind).strip().lower() != "web_h5":
        return bool(initial_skip)
    try:
        cfg = config_manager.get_device_config(ip)
        ws_enabled = bool((cfg.get("ws_token") or {}).get("enabled", False))
        if not ws_enabled:
            return bool(initial_skip)
        return bool(bot_state.get_ws_h5_handoff_ok(ip))
    except Exception:
        return False
```

This deliberately ignores `initial_skip=True` when WS is enabled and the handoff is unhealthy: a kicked or aborted WS run must trigger a fresh online check.

- [ ] **Step 5: Wire the resolver into the main loop**

Add `resolve_skip_online_check_once` to the existing import from `runtime_services.web_session_service` in `new_main_v2.py`.

Immediately before `handle_device_wakeup()`, calculate the current-cycle value and pass it instead of the initialization-only flag:

```python
                skip_online_check_for_wakeup = resolve_skip_online_check_once(
                    ip,
                    backend_kind,
                    initial_skip=skip_online_check_once,
                )
                d = handle_device_wakeup(
                    d,
                    ip,
                    logger,
                    Cnn_model,
                    skip_online_check_once=skip_online_check_for_wakeup,
                )
```

Keep the existing `skip_online_check_once = False` assignment after wakeup so the initialization flag remains one-shot.

- [ ] **Step 6: Run resolver and wiring tests and verify GREEN**

Run:

```powershell
conda run -n mushroom1 python -m pytest tests/test_checker_gate_web_launch.py tests/test_h5_ws_startup_order.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the runtime wiring**

```powershell
git add runtime_services/web_session_service.py new_main_v2.py tests/test_checker_gate_web_launch.py tests/test_h5_ws_startup_order.py
git commit -m "fix(runtime): skip self-online check after healthy WS handoff"
```

### Task 4: Focused Regression Verification

**Files:**
- Verify: `bot_state.py`
- Verify: `game_actions/ws_phase.py`
- Verify: `runtime_services/web_session_service.py`
- Verify: `new_main_v2.py`
- Verify: related tests only

- [ ] **Step 1: Run the complete focused regression set**

```powershell
conda run -n mushroom1 python -m pytest tests/test_bot_state_ws_h5_handoff.py tests/test_ws_phase.py tests/test_browser_skip.py tests/test_checker_gate_web_launch.py tests/test_h5_ws_startup_order.py tests/test_wake_home_order.py -q
```

Expected: all focused tests pass with no failures.

- [ ] **Step 2: Compile only the changed Python files**

```powershell
conda run -n mushroom1 python -m py_compile bot_state.py game_actions/ws_phase.py runtime_services/web_session_service.py new_main_v2.py tests/test_bot_state_ws_h5_handoff.py tests/test_ws_phase.py tests/test_checker_gate_web_launch.py tests/test_h5_ws_startup_order.py
```

Expected: exit code 0 and no output.

- [ ] **Step 3: Check the final diff and worktree state**

```powershell
git diff --check HEAD~3..HEAD
git status --short --branch
```

Expected: no whitespace errors; branch contains only the design/plan and scoped implementation commits, with no unrelated source changes.

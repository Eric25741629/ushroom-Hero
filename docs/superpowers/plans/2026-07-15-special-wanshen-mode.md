# Special Wanshen Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted, dashboard-controlled Wanshen-only mode for 寶兒 (`web-001`) and 暴哥 (`web-002`) that skips every other script and attempts one 10-round run per Taiwan day from Tuesday through Saturday between 03:00 and 06:59.

**Architecture:** A focused `game_actions.special_wanshen` module owns schedule/state decisions and the single Wanshen execution. `daily_pipeline.run` routes marked accounts into that module before the general task sequence, while `new_main_v2._run_ws_phase_for_wake` suppresses WS tasks for the same accounts. Config/API/status fields are authoritative; the dashboard renders a dedicated one-task card without changing normal devices.

**Tech Stack:** Python 3, Flask, pytest, existing `config_manager`, `json_manager.JsonDataManager`, Playwright-backed `new_battle.fight_test`, vanilla HTML/CSS/JavaScript dashboard.

---

## File map

- Create `game_actions/special_wanshen.py`: pure schedule helpers, record inspection, dashboard state, and execution orchestration.
- Create `tests/test_special_wanshen.py`: schedule, once-per-day, weekly completion, and execution-result tests.
- Modify `config_manager.py`: defaults, typed fields, and sanitization for the three special-mode settings.
- Modify `bot_config.json`: mark `web-001` and `web-002` as special accounts, enable their runtime threads, start mode disabled, and set 10 rounds.
- Modify `game_actions/daily_pipeline.py`: hard route special accounts before the general pipeline.
- Modify `new_main_v2.py`: skip WS-first tasks for special accounts.
- Modify `tests/test_daily_pipeline.py`: prove general tasks are bypassed.
- Create `tests/test_special_wanshen_runtime.py`: prove `_run_ws_phase_for_wake` does not invoke WS work and special accounts do not skip browser startup.
- Modify `control_panel/routes_config.py`: add `GET/POST /api/special_wanshen/<ip>`.
- Modify `control_panel/routes_status.py`: expose special-mode state for running and disabled cards.
- Create `tests/test_special_wanshen_api.py`: API persistence, validation, and status contract.
- Modify `templates/dashboard.html`: single Wanshen badge, persisted state button, special idle-web action, and state colors.
- Modify `tests/test_dashboard_template.py`: lock special rendering and button strings.

### Task 1: Config schema and special account defaults

**Files:**
- Modify: `config_manager.py`
- Modify: `bot_config.json`
- Test: `tests/test_special_wanshen.py`

- [ ] **Step 1: Write failing config tests**

Add tests that assert defaults and sanitization:

```python
def test_special_wanshen_config_defaults():
    cfg = config_manager.DeviceConfig.from_dict({})
    assert cfg.get("special_wanshen_account") is False
    assert cfg.get("special_wanshen_enabled") is False
    assert cfg.get("special_wanshen_rounds") == 10


def test_special_wanshen_rounds_are_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(tmp_path / "bot_config.json"))
    config_manager.update_device_config("web-x", {
        "special_wanshen_account": True,
        "special_wanshen_enabled": True,
        "special_wanshen_rounds": 999,
    })
    cfg = config_manager.get_device_config("web-x")
    assert cfg.get("special_wanshen_account") is True
    assert cfg.get("special_wanshen_enabled") is True
    assert cfg.get("special_wanshen_rounds") == 50
```

- [ ] **Step 2: Run the config tests and verify RED**

Run: `python -m pytest tests/test_special_wanshen.py -q`

Expected: FAIL because the special config fields do not exist.

- [ ] **Step 3: Add defaults, dataclass fields, and sanitization**

Add to `DEFAULT_DEVICE_CONFIG` and `DeviceConfig`:

```python
"special_wanshen_account": False,
"special_wanshen_enabled": False,
"special_wanshen_rounds": 10,
```

Normalize booleans with the existing `_to_bool` path and clamp rounds with:

```python
current["special_wanshen_rounds"] = _clamp_int(
    current.get("special_wanshen_rounds"), 1, 50, 10
)
```

Set the following on both `web-001` and `web-002` in `bot_config.json`:

```json
"enabled": true,
"special_wanshen_account": true,
"special_wanshen_enabled": false,
"special_wanshen_rounds": 10
```

- [ ] **Step 4: Run the config tests and verify GREEN**

Run: `python -m pytest tests/test_special_wanshen.py -q`

Expected: PASS.

- [ ] **Step 5: Commit config support**

```powershell
git add config_manager.py bot_config.json tests/test_special_wanshen.py
git commit -m "feat(wanshen): add special account config"
```

### Task 2: Schedule, records, and 10-round execution

**Files:**
- Create: `game_actions/special_wanshen.py`
- Modify: `tests/test_special_wanshen.py`

- [ ] **Step 1: Write failing schedule and execution tests**

Cover the exact time boundaries and result semantics:

```python
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


@pytest.mark.parametrize("hour", [3, 4, 5, 6])
def test_due_tuesday_to_saturday_during_early_window(hour):
    now = datetime.datetime(2026, 7, 7, hour, 30, tzinfo=TAIPEI)
    assert special_wanshen.is_due(
        now=now, account=True, enabled=True,
        attempted_today=False, completed_this_week=False,
    ) is True


@pytest.mark.parametrize("now", [
    datetime.datetime(2026, 7, 7, 2, 59, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 7, 7, 0, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 6, 4, 0, tzinfo=TAIPEI),
    datetime.datetime(2026, 7, 12, 4, 0, tzinfo=TAIPEI),
])
def test_not_due_outside_day_or_time_window(now):
    assert special_wanshen.is_due(
        now=now, account=True, enabled=True,
        attempted_today=False, completed_this_week=False,
    ) is False


def test_attempt_is_recorded_before_fight_and_success_records_week(fake_manager):
    calls = []
    result = special_wanshen.run_if_due(
        object(), "web-001", cfg={
            "special_wanshen_account": True,
            "special_wanshen_enabled": True,
            "special_wanshen_rounds": 10,
        },
        now=datetime.datetime(2026, 7, 7, 4, 0, tzinfo=TAIPEI),
        manager=fake_manager,
        fight_fn=lambda d, rounds: calls.append(rounds) or True,
    )
    assert calls == [10]
    assert fake_manager.recorded == ["萬神專用_嘗試", "萬神專用_完成"]
    assert result["completed_this_week"] is True
```

Also assert failed/raised fights write only `萬神專用_嘗試`, and an existing daily attempt or weekly completion prevents calling `fight_fn`.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_special_wanshen.py -q`

Expected: FAIL with missing `game_actions.special_wanshen`.

- [ ] **Step 3: Implement the focused module**

Use these public boundaries:

```python
ATTEMPT_RECORD = "萬神專用_嘗試"
COMPLETE_RECORD = "萬神專用_完成"
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))


def is_due(*, now, account, enabled, attempted_today, completed_this_week):
    local = now.astimezone(TAIPEI)
    return bool(
        account and enabled
        and 1 <= local.weekday() <= 5
        and 3 <= local.hour < 7
        and not attempted_today
        and not completed_this_week
    )


def get_status(ip, *, cfg=None, now=None, manager=None) -> dict:
    """Return config flags plus daily-attempt and weekly-completion state."""
    cfg = cfg or config_manager.get_device_config(ip)
    now = _taipei_now(now)
    manager = manager or JsonDataManager(ip)
    attempted_today = _record_matches_day(manager, ATTEMPT_RECORD, now.date())
    completed_this_week = _record_matches_iso_week(manager, COMPLETE_RECORD, now)
    return _build_status(cfg, now, attempted_today, completed_this_week)


def run_if_due(d, ip, *, cfg=None, now=None, manager=None, fight_fn=None) -> dict:
    """Attempt one configured run when due and return refreshed status."""
    cfg = cfg or config_manager.get_device_config(ip)
    now = _taipei_now(now)
    manager = manager or JsonDataManager(ip)
    status = get_status(ip, cfg=cfg, now=now, manager=manager)
    if not status["due"]:
        return status
    manager.record_timestamp(ATTEMPT_RECORD)
    succeeded = False
    try:
        succeeded = bool((fight_fn or fight_test)(d, status["rounds"]))
    except Exception:
        logger.exception("[%s] 萬神專用流程執行失敗", ip)
    if succeeded:
        manager.record_timestamp(COMPLETE_RECORD)
    return get_status(ip, cfg=cfg, now=now, manager=manager)
```

`get_status` reads records through `JsonDataManager`; `run_if_due` records the attempt before invoking `fight_fn`, records completion only on `True`, updates `bot_state`, logs failures, and returns the refreshed state.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_special_wanshen.py -q`

Expected: PASS.

- [ ] **Step 5: Commit scheduler core**

```powershell
git add game_actions/special_wanshen.py tests/test_special_wanshen.py
git commit -m "feat(wanshen): schedule special ten-round runs"
```

### Task 3: Isolate the runtime from general ADB/H5 and WS tasks

**Files:**
- Modify: `game_actions/daily_pipeline.py`
- Modify: `new_main_v2.py`
- Modify: `game_actions/browser_skip.py`
- Modify: `tests/test_daily_pipeline.py`
- Create: `tests/test_special_wanshen_runtime.py`

- [ ] **Step 1: Write failing isolation tests**

Add a daily pipeline test that stubs the special runner and proves `_run_tasks` is not called:

```python
def test_special_account_routes_before_general_pipeline(monkeypatch, pipeline_mod, minimal_ctx):
    calls = []
    monkeypatch.setattr(pipeline_mod.config_manager, "get_device_config",
                        lambda ip: {"special_wanshen_account": True})
    monkeypatch.setattr(pipeline_mod.special_wanshen, "run_if_due",
                        lambda *a, **k: calls.append("special"))
    monkeypatch.setattr(pipeline_mod, "_run_tasks",
                        lambda ctx: calls.append("general"))
    pipeline_mod.run(minimal_ctx)
    assert calls == ["special"]
```

Add focused tests that `_run_ws_phase_for_wake` returns an empty set without calling `run_ws_phase`, and `browser_skip.should_skip_browser` returns `False` for special accounts.

- [ ] **Step 2: Run isolation tests and verify RED**

Run: `python -m pytest tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py -q`

Expected: FAIL because no special routing exists.

- [ ] **Step 3: Implement early routing**

At the top of `daily_pipeline.run`:

```python
cfg = config_manager.get_device_config(ctx.ip)
if cfg.get("special_wanshen_account", False):
    special_wanshen.run_if_due(ctx.d, ctx.ip, cfg=cfg)
    return
```

In `_run_ws_phase_for_wake`, acquire the scheduler lease as today, then return `frozenset()` before any WS task if `special_wanshen_account` is true. In `browser_skip.should_skip_browser`, return `False` for special accounts so the early-hours run can always obtain the H5 page.

- [ ] **Step 4: Run isolation tests and verify GREEN**

Run: `python -m pytest tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit runtime isolation**

```powershell
git add game_actions/daily_pipeline.py new_main_v2.py game_actions/browser_skip.py tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py
git commit -m "feat(wanshen): isolate special accounts from normal tasks"
```

### Task 4: Persisted API and dashboard status

**Files:**
- Modify: `control_panel/routes_config.py`
- Modify: `control_panel/routes_status.py`
- Create: `tests/test_special_wanshen_api.py`

- [ ] **Step 1: Write failing API tests**

Test GET, POST enable/disable, and rejection:

```python
def test_enable_special_wanshen_persists(client, configured_special_device):
    response = client.post("/api/special_wanshen/web-001", json={"enabled": True})
    assert response.status_code == 200
    body = response.get_json()
    assert body["enabled"] is True
    assert config_manager.get_device_config("web-001").get("special_wanshen_enabled") is True
    assert config_manager.get_device_config("web-001").get("enabled") is True


def test_non_special_device_is_rejected(client, configured_normal_device):
    response = client.post("/api/special_wanshen/normal", json={"enabled": True})
    assert response.status_code == 403
```

Assert GET returns `rounds`, `attempted_today`, `completed_this_week`, and `next_attempt_at`; assert `/api/status` includes the same core flags for both running and disabled cards.

- [ ] **Step 2: Run API tests and verify RED**

Run: `python -m pytest tests/test_special_wanshen_api.py -q`

Expected: FAIL with 404 for the new route.

- [ ] **Step 3: Implement routes and status fields**

Add to `routes_config.py`:

```python
@bp.route("/api/special_wanshen/<ip>", methods=["GET", "POST"])
def special_wanshen_config(ip):
    require_device_access(ip)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    cfg = config_manager.get_device_config(real_ip)
    if not cfg.get("special_wanshen_account", False):
        return jsonify({"status": "error", "message": "此帳號不是萬神專用帳號"}), 403
    if request.method == "POST":
        enabled = bool((request.get_json(silent=True) or {}).get("enabled", False))
        update = {"special_wanshen_enabled": enabled}
        if enabled:
            update["enabled"] = True
        config_manager.update_device_config(real_ip, update)
    return jsonify(special_wanshen.get_status(real_ip))
```

Expose `special_wanshen_account`, `special_wanshen_enabled`, `special_wanshen_rounds`, `special_wanshen_attempted_today`, and `special_wanshen_completed_this_week` in `routes_status.py`.

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `python -m pytest tests/test_special_wanshen_api.py tests/test_smoke_config_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit API support**

```powershell
git add control_panel/routes_config.py control_panel/routes_status.py tests/test_special_wanshen_api.py
git commit -m "feat(wanshen): expose persisted special mode API"
```

### Task 5: Render the dedicated dashboard controls

**Files:**
- Modify: `templates/dashboard.html`
- Modify: `tests/test_dashboard_template.py`

- [ ] **Step 1: Write failing template assertions**

Add assertions for the three labels, CSS states, endpoint, and special progress branch:

```python
def test_special_wanshen_dashboard_controls_exist():
    html = _html()
    assert "跑萬神試煉・未啟用" in html
    assert "跑萬神試煉・已啟用" in html
    assert "本週已完成，不再執行" in html
    assert "/api/special_wanshen/" in html
    assert "special_wanshen_account" in html
    assert "special_wanshen_completed_this_week" in html
    assert "manual_hold_until_closed: false" in html
```

- [ ] **Step 2: Run template test and verify RED**

Run: `python -m pytest tests/test_dashboard_template.py -q`

Expected: FAIL because the labels and special branch are absent.

- [ ] **Step 3: Implement special rendering and toggle**

Add `.btn-wanshen-off`, `.btn-wanshen-on`, and `.btn-wanshen-done` styles. In `fetchStatus` rendering:

```javascript
const specialWanshen = info.special_wanshen_account === true;
if (specialWanshen) {
  const done = info.special_wanshen_completed_this_week === true;
  pc.innerHTML = `<span class="task-badge ${done ? 'done' : ''}">${done ? '✅' : '⏳'} 萬神試煉</span>`;
} else {
  // existing progress rendering
}
```

Render only the idle-web control and special toggle in the action bar for special accounts. Add:

```javascript
async function toggleSpecialWanshen(ip, enabled) {
  const resp = await fetch(`/api/special_wanshen/${ip}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({enabled})
  });
  if (!resp.ok) throw new Error((await resp.json()).message || 'toggle failed');
  await fetchStatus();
}
```

Allow `launchWebPage(ip, specialIdle)` and send `{manual_hold_until_closed:false}` when `specialIdle` is true.

- [ ] **Step 4: Run dashboard tests and verify GREEN**

Run: `python -m pytest tests/test_dashboard_template.py tests/test_special_wanshen_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit dashboard UI**

```powershell
git add templates/dashboard.html tests/test_dashboard_template.py
git commit -m "feat(dashboard): add special Wanshen toggle"
```

### Task 6: Final targeted verification

**Files:**
- Verify all files modified above.

- [ ] **Step 1: Run focused regression tests**

Run:

```powershell
python -m pytest tests/test_special_wanshen.py tests/test_special_wanshen_runtime.py tests/test_special_wanshen_api.py tests/test_daily_pipeline.py tests/test_dashboard_template.py tests/test_task_due.py tests/test_dungeon_scheduler.py tests/test_smoke_config_api.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax checks**

Run:

```powershell
python -m py_compile game_actions/special_wanshen.py game_actions/daily_pipeline.py game_actions/browser_skip.py new_main_v2.py control_panel/routes_config.py control_panel/routes_status.py tests/test_special_wanshen.py tests/test_special_wanshen_runtime.py tests/test_special_wanshen_api.py
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check HEAD~5..HEAD` and `git status --short`.

Expected: no whitespace errors; only intended files are modified or committed.

- [ ] **Step 4: Record final verification commit only if needed**

If verification requires a small test-only correction, commit it with:

```powershell
git add tests
git commit -m "test(wanshen): complete special mode coverage"
```

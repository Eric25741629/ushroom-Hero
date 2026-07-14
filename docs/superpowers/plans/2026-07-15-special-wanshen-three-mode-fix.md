# Special Wanshen Three-Mode Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore three mode controls plus active-mode exception controls for special Wanshen accounts, make full automation and Wanshen-only execution mutually exclusive, and collapse disabled cards to their header and mode controls.

**Architecture:** Derive a single `off` / `full` / `wanshen` mode from the existing flags and switch it atomically. Full mode keeps the normal persistent runtime. Wanshen mode is a scanner-gated one-shot job: claim the daily attempt before browser startup, require the main page, run once, close, and exit without the hourly sleep loop.

**Tech Stack:** Python 3, Flask, pytest, existing config manager and runtime pipeline, vanilla HTML/CSS/JavaScript.

---

## File map

- Modify `game_actions/special_wanshen.py`: derive and validate the three modes.
- Modify `control_panel/routes_config.py`: atomically switch modes and keep the legacy boolean endpoint compatible.
- Modify `control_panel/routes_status.py`: expose `special_wanshen_mode`.
- Modify `game_actions/daily_pipeline.py`, `new_main_v2.py`, and `game_actions/browser_skip.py`: isolate normal tasks only while Wanshen mode is active.
- Modify `runtime_services/device_scan_service.py`: create a Wanshen thread only while the one-shot schedule is due.
- Modify `templates/dashboard.html`: render exactly three controls and collapse off-mode details.
- Modify `tests/test_special_wanshen.py`, `tests/test_special_wanshen_api.py`, `tests/test_special_wanshen_runtime.py`, and `tests/test_dashboard_template.py`: lock the mode state machine, routing, and presentation.

### Task 1: Authoritative mode state and atomic API

**Files:**
- Modify: `game_actions/special_wanshen.py`
- Modify: `control_panel/routes_config.py`
- Modify: `control_panel/routes_status.py`
- Modify: `tests/test_special_wanshen.py`
- Modify: `tests/test_special_wanshen_api.py`

- [ ] **Step 1: Write failing mode tests**

Add to `tests/test_special_wanshen.py`:

```python
@pytest.mark.parametrize(("cfg", "expected"), [
    ({"special_wanshen_account": True, "enabled": False,
      "special_wanshen_enabled": False}, "off"),
    ({"special_wanshen_account": True, "enabled": True,
      "special_wanshen_enabled": False}, "full"),
    ({"special_wanshen_account": True, "enabled": True,
      "special_wanshen_enabled": True}, "wanshen"),
])
def test_get_mode_derives_mutually_exclusive_state(cfg, expected):
    assert special_wanshen.get_mode(cfg) == expected


def test_mode_settings_write_both_flags_atomically():
    assert special_wanshen.mode_settings("off") == {
        "enabled": False, "special_wanshen_enabled": False,
    }
    assert special_wanshen.mode_settings("full") == {
        "enabled": True, "special_wanshen_enabled": False,
    }
    assert special_wanshen.mode_settings("wanshen") == {
        "enabled": True, "special_wanshen_enabled": True,
    }
    with pytest.raises(ValueError):
        special_wanshen.mode_settings("invalid")
```

Add API tests to `tests/test_special_wanshen_api.py` that POST each mode to `/api/special_wanshen_mode/web-001`, assert both persisted flags, assert invalid mode returns 400, and assert a normal account returns 403. Extend the status assertion with:

```python
assert bots["web-001"]["special_wanshen_mode"] == "full"
assert bots["web-002"]["special_wanshen_mode"] == "off"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_special_wanshen.py tests/test_special_wanshen_api.py tests/test_daily_pipeline.py -q
```

Expected: failures for missing `get_mode`, `mode_settings`, mode endpoint, and status field.

- [ ] **Step 3: Implement the mode helpers**

Add to `game_actions/special_wanshen.py`:

```python
MODE_OFF = "off"
MODE_FULL = "full"
MODE_WANSHEN = "wanshen"
_MODE_SETTINGS = {
    MODE_OFF: {"enabled": False, "special_wanshen_enabled": False},
    MODE_FULL: {"enabled": True, "special_wanshen_enabled": False},
    MODE_WANSHEN: {"enabled": True, "special_wanshen_enabled": True},
}


def get_mode(cfg: Any) -> str:
    if bool(cfg.get("enabled", True)):
        if bool(cfg.get("special_wanshen_enabled", False)):
            return MODE_WANSHEN
        return MODE_FULL
    return MODE_OFF


def mode_settings(mode: str) -> dict:
    normalized = str(mode or "").strip().lower()
    if normalized not in _MODE_SETTINGS:
        raise ValueError("mode 必須是 off、full 或 wanshen")
    return dict(_MODE_SETTINGS[normalized])
```

Include `"mode": get_mode(cfg)` in `get_status`.

- [ ] **Step 4: Implement the atomic API and status field**

Add this route to `control_panel/routes_config.py`:

```python
@bp.route("/api/special_wanshen_mode/<ip>", methods=["POST"])
def set_special_wanshen_mode(ip):
    require_device_access(ip)
    real_ip = ip.split(":")[-1] if ":" in ip else ip
    cfg = config_manager.get_device_config(real_ip)
    if not cfg.get("special_wanshen_account", False):
        return jsonify({"status": "error", "message": "此帳號不是萬神專用帳號"}), 403
    try:
        settings = special_wanshen.mode_settings(
            (request.get_json(silent=True) or {}).get("mode")
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    config_manager.update_device_config(real_ip, settings)
    updated = config_manager.get_device_config(real_ip)
    return jsonify(special_wanshen.get_status(real_ip, cfg=updated))
```

Change the legacy boolean POST so `True` applies `mode_settings("wanshen")` and `False` applies `mode_settings("off")`. Add `special_wanshen_mode` to `_special_wanshen_fields` for both normal and special accounts; special accounts use `status["mode"]`.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_special_wanshen.py tests/test_special_wanshen_api.py tests/test_daily_pipeline.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the state machine**

```powershell
git add game_actions/special_wanshen.py control_panel/routes_config.py control_panel/routes_status.py tests/test_special_wanshen.py tests/test_special_wanshen_api.py
git commit -m "fix(wanshen): add mutually exclusive script modes"
```

### Task 2: Restore full mode and make Wanshen a one-shot job

**Files:**
- Modify: `game_actions/daily_pipeline.py`
- Modify: `new_main_v2.py`
- Modify: `game_actions/browser_skip.py`
- Modify: `runtime_services/device_scan_service.py`
- Modify: `tests/test_daily_pipeline.py`
- Modify: `tests/test_special_wanshen_runtime.py`

- [ ] **Step 1: Write failing routing tests**

Replace the daily special routing test with two cases: a pre-claimed Wanshen context checks `get_stage_with_check` and calls only `run_claimed` when the stage is `主頁面`; full mode calls only `_run_tasks`. Add a non-main-page case proving no fight starts. In `tests/test_special_wanshen_runtime.py`, add full-mode cases proving WS and browser-skip behavior are restored, scanner cases proving Wanshen starts only while due, and claim cases proving a second claim on the same day is rejected.

Use this config split in every test:

```python
WANSHEN_CFG = {
    "special_wanshen_account": True,
    "special_wanshen_enabled": True,
}
FULL_CFG = {
    "special_wanshen_account": True,
    "special_wanshen_enabled": False,
}
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py -q
```

Expected: full-mode cases fail because the capability flag still intercepts runtime work; claim and scanner tests fail because the one-shot boundaries do not exist.

- [ ] **Step 3: Narrow all runtime gates to active Wanshen mode**

In each production file replace the account-only condition with:

```python
bool(cfg.get("special_wanshen_account", False)) and bool(
    cfg.get("special_wanshen_enabled", False)
)
```

Use `cfg` in `daily_pipeline.py`, `device_cfg` in `new_main_v2.py`, and `cfg` in `browser_skip.py`. This preserves Wanshen-only isolation while allowing `full` mode to use the normal pipeline, WS-first phase, and browser-skip policy.

Add `claim_if_due` and `run_claimed` to `game_actions/special_wanshen.py`. `claim_if_due` refreshes status, writes `ATTEMPT_RECORD` before browser startup, and returns a boolean. `run_claimed` runs the supplied fight function without writing a second attempt and records completion only on success. Keep `run_if_due` as a compatibility wrapper that claims and then delegates.

Add `special_wanshen_claimed: bool = False` to `DailyContext`. At the start of `new_main_v2.main`, active Wanshen mode calls `claim_if_due`; false returns immediately. Pass the claim to `DailyContext`. In `daily_pipeline.run`, the Wanshen branch checks `get_stage_with_check`; only `主頁面` calls `run_claimed`. After the pipeline, and before any caught failure would enter sleep, active Wanshen mode stops the web runtime and returns, ending the thread.

In `runtime_services/device_scan_service.py`, before starting a special Wanshen device, read `special_wanshen.get_status(ip, cfg=cfg)` and skip thread creation unless `due` is true. Full and normal devices keep existing scanner behavior.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit runtime routing**

```powershell
git add game_actions/special_wanshen.py game_actions/daily_pipeline.py new_main_v2.py game_actions/browser_skip.py runtime_services/device_scan_service.py tests/test_daily_pipeline.py tests/test_special_wanshen_runtime.py
git commit -m "fix(wanshen): run special schedule once per day"
```

### Task 3: Render three mode controls, exception controls, and collapsed off mode

**Files:**
- Modify: `templates/dashboard.html`
- Modify: `tests/test_dashboard_template.py`

- [ ] **Step 1: Write failing dashboard assertions**

Extend `test_special_wanshen_dashboard_controls_exist` with:

```python
assert "啟用完整腳本" in html
assert "完整腳本・已啟用" in html
assert "/api/special_wanshen_mode/" in html
assert "special-wanshen-off" in html
assert "setSpecialWanshenMode" in html
```

Add a source-order assertion that the special branch builds `fullScriptBtn`, `idleWebBtn`, and `wanshenBtn`, then assigns exactly those three values to `actionBar.innerHTML`.

Also assert the active-mode branch includes the existing exception handlers and labels:

```python
assert "deviceControl('${ip}','pause')" in html
assert "deviceControl('${ip}','resume')" in html
assert "launchWebPage('${ip}')" in html
assert "openLiveView('${ip}')" in html
assert "skipSleep('${ip}')" in html
assert "specialExceptionControls" in html
```

- [ ] **Step 2: Run the template test and verify RED**

Run:

```powershell
python -m pytest tests/test_dashboard_template.py -q
```

Expected: failures for the restored full-script labels, mode endpoint, and compact-card class.

- [ ] **Step 3: Add compact-card CSS and authoritative mode rendering**

Add:

```css
.card.special-wanshen-off .wake-block,
.card.special-wanshen-off .info-grid,
.card.special-wanshen-off .carpark-detail,
.card.special-wanshen-off .progress-container,
.card.special-wanshen-off .log-box {
  display: none !important;
}
```

At the start of each card update derive:

```javascript
const specialWanshen = info.special_wanshen_account === true;
const specialMode = info.special_wanshen_mode || 'off';
const specialWanshenActive = specialWanshen && specialMode === 'wanshen';
const specialFullActive = specialWanshen && specialMode === 'full';
```

Append `special-wanshen-off` to `card.className` only when the account is special and `specialMode === 'off'`. For progress, Wanshen mode shows only the Wanshen badge, full mode calls the existing `fetchProgress`, and off mode clears the progress HTML.

- [ ] **Step 4: Render exactly three buttons and atomic transitions**

In the special account action branch build:

```javascript
const fullScriptBtn = specialFullActive
  ? `<button class="btn btn-wanshen-on" onclick="setSpecialWanshenMode('${ip}', 'off')">✓ 完整腳本・已啟用</button>`
  : `<button class="btn btn-resume" onclick="setSpecialWanshenMode('${ip}', 'full')">✅ 啟用完整腳本</button>`;
const idleWebBtn = isDisabled
  ? `<button class="btn btn-skip" onclick="openWebForSetup('${ip}')">🌐 開啟網頁掛機(不跑任何腳本，僅刷小怪用)</button>`
  : (webOpen
      ? `<button class="btn btn-force" onclick="closeWebPage('${ip}')">關閉網頁掛機</button>`
      : `<button class="btn btn-skip" onclick="launchWebPage('${ip}', true)">🌐 開啟網頁掛機(不跑任何腳本，僅刷小怪用)</button>`);
const wanshenBtn = specialWanshenDone
  ? `<button class="btn btn-wanshen-done" disabled>✓ 本週已完成，不再執行</button>`
  : (specialWanshenActive
      ? `<button class="btn btn-wanshen-on" onclick="setSpecialWanshenMode('${ip}', 'off')">✓ 跑萬神試煉・已啟用</button>`
      : `<button class="btn btn-wanshen-off" onclick="setSpecialWanshenMode('${ip}', 'wanshen')">× 跑萬神試煉・未啟用</button>`);
const specialExceptionControls = specialMode === 'off' ? '' : `
  ${isPaused
    ? `<button class="btn btn-resume" onclick="deviceControl('${ip}','resume')">恢復</button>`
    : `<button class="btn btn-pause" onclick="deviceControl('${ip}','pause')">暫停</button>`}
  ${showWebLaunch
    ? (webOpen
        ? `<button class="btn btn-force" onclick="closeWebPage('${ip}')">關閉網頁</button>`
        : `<button class="btn btn-skip" onclick="launchWebPage('${ip}')">開啟網頁</button>`)
    : ''}
  ${showLiveView ? `<button class="btn btn-resume" onclick="openLiveView('${ip}')">遠端畫面</button>` : ''}
  <button class="btn btn-skip" onclick="skipSleep('${ip}')">跳過睡眠</button>`;
actionBar.innerHTML = `${fullScriptBtn}${idleWebBtn}${wanshenBtn}${specialExceptionControls}`;
```

Preserve the existing control-button and web-button lock attributes when applying the final implementation. Replace `toggleSpecialWanshen` with:

```javascript
async function setSpecialWanshenMode(ip, mode) {
  try {
    const resp = await fetch(`/api/special_wanshen_mode/${ip}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode })
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(result.message || '腳本模式更新失敗');
    await fetchStatus();
  } catch (e) {
    window.UI.toast('錯誤：' + e, 'err');
  }
}
```

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_dashboard_template.py tests/test_special_wanshen_api.py tests/test_daily_pipeline.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit the dashboard correction**

```powershell
git add templates/dashboard.html tests/test_dashboard_template.py
git commit -m "fix(dashboard): restore three Wanshen account controls"
```

### Task 4: Final targeted verification

**Files:**
- Verify all files modified in Tasks 1–3.

- [ ] **Step 1: Run the complete focused regression set**

```powershell
python -m pytest tests/test_special_wanshen.py tests/test_special_wanshen_runtime.py tests/test_special_wanshen_api.py tests/test_daily_pipeline.py tests/test_dashboard_template.py tests/test_task_due.py tests/test_dungeon_scheduler.py tests/test_smoke_config_api.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax checks**

```powershell
python -m py_compile game_actions/special_wanshen.py game_actions/daily_pipeline.py game_actions/browser_skip.py new_main_v2.py control_panel/routes_config.py control_panel/routes_status.py tests/test_special_wanshen.py tests/test_special_wanshen_runtime.py tests/test_special_wanshen_api.py
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Inspect repository state**

```powershell
git diff --check 705c0c9b..HEAD
git status --short
```

Expected: no whitespace errors and no unintended uncommitted files.

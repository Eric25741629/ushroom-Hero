# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

菇勇者全自動掛機 - A multi-device automation bot for a mobile H5 game. Supports two backends:
- `adb`: Direct device/emulator control via `uiautomator2`
- `web_h5`: Playwright-based browser automation for H5 game

## Working Style (本專案工作慣例 — 使用者 2026-06-09 指定，務必遵守)

- **多用 todolist**：任何多步驟工作用 Task 工具（TaskCreate/TaskUpdate）+ `tasks/todo.md` 追蹤進度。
- **規劃與實作都走 `tasks/todo.md`**：先把 plan 寫進去，實作時逐項標完成。
- **memory 一律用英文寫**（`~/.claude/.../memory/*.md`）。
- **複雜或大 context 的任務用 subagents 做上下文隔離**：自行判斷是否需要大量子代理；把研究 / 探索 / 實作 offload 給 subagent，保持主 context 乾淨。
- 動到正在跑的 bot（`new_main_v2.py` / `device_wrapper.py` / 排程）的大改動：先把 plan 寫進 `tasks/todo.md` 並讓使用者過目，再動手。
- **Subagent 一律 Opus**：spawn Agent 時加 `model:"opus"`。
- **Subagent 檔案所有權**：禁止 subagent 用 Write 覆寫共用文件（`todo.md` 等）；用 Edit、列出 owned files、fan-out 前先 commit baseline。
- **code-editing 走 worktree 隔離**：使用者同時跑多個 Claude Code；改程式碼的 session 開自己的 branch + worktree，完成後 merge 回 main 再刪 worktree + branch。
- **每段落自動 commit**：做完一個段落就 commit，不用問；只 stage 有動到的檔案（絕不 `git add -A`：~80 WIP 檔 + `auth_state/` secrets）；不 push、不加 attribution footer。

## 導覽索引 (Navigation Index)

> **先看這裡再開工。** 完整程式碼地圖 + 文件總覽 + 「我想做 X → 看這裡」快查表：[`docs/INDEX.md`](docs/INDEX.md)。
> 跨子系統重構/優化待辦（已驗證）：[`docs/REFACTORING_OPPORTUNITIES.md`](docs/REFACTORING_OPPORTUNITIES.md)。
> 狀態管理（bot_state）重構展開與進度：[`docs/REFACTOR_STATE_MANAGEMENT.md`](docs/REFACTOR_STATE_MANAGEMENT.md)。
> 每子系統優化分析：根目錄 `OPTIMIZE_*.md`（6 份，2026-05）。

### 本機 Hooks（`.claude/hooks`，已 gitignore，不進版控）
| Hook | 時機 | 作用 |
|------|------|------|
| `.claude/hooks/py_check.py` | PostToolUse (`Edit\|Write\|MultiEdit`) | 編輯 `.py` 後跑 `py_compile`（語法錯 exit 2 擋下）+ 非阻塞 ruff 報告 |
| `.claude/hooks/check_pytest.py` | PreToolUse (`Bash`) | 擋裸 `pytest`（無 file/`::`/path/`-k` target），避免 import 真實 device/Playwright/OCR 而 hang |

### 常用可復用工具（動手前先確認有沒有現成 helper，勿重造輪子）
| 用途 | 用這個 |
|------|--------|
| per-device logger | `utils/logging_utils.setup_logger_for_device` / `logger` proxy |
| log 路徑 | `utils/log_paths.LogPaths`（勿硬編 `logs/<device>/...`，`with_root()` 給測試沙箱） |
| 出事抓圖 | `utils/screenshot_helpers.save_error_screenshot` → `utils/smart_screenshot.SmartScreenshotRecorder` |
| 直接驅動 Playwright 的任務 | `utils/pause_guard`（bind/check/unbind，否則 live-view 手動接管無法中斷） |
| 共用 CNN forward | `utils/torch_runtime.inference_slot()`（序列化 GPU）+ `configure_torch_runtime()` |
| NAS 權重載入 | `utils/model_sync.ensure_local_model()`（`torch.load` 前先呼叫） |
| web_h5 遊戲 RPC / protobuf | `utils/web_game_api.WebGameAPI.call_raw` / `_walk_pb` |
| 裝置狀態路由 key | `bot_state.is_local_device`（勿用 `':' in ip`） |

## Entry Points

| File | Purpose |
|------|--------|
| `new_main_v2.py` | Main entry - scans devices, spawns threads per device |
| `control_panel_app.py` | Flask central control dashboard (port 5002); now a thin façade — routes live in `control_panel/` blueprints (`routes_status/control/config/worker/web_session/live_view/labeler/fly_pet/pages` + `shared/`) |
| `config_manager.py` | Configuration loader with host-specific overrides |

## Core Architecture

### Multi-Device Execution Model

```python
# Main loop in new_main_v2.py:
while True:
    devices = get_adb_devices()  # Scan ADB devices
    for ip in devices:
        spawn_thread(main, ip, ...)  # One thread per device
```

Each device thread runs an independent automation loop with:
- Per-device logger (`logs/{ip}.log`)
- Per-device state tracking (`bot_state.py`)
- Shared OCR server fallback (`img_tools.py`)

### Master/Worker Pattern

- **Master**: Runs control panel, maintains local state, receives worker reports
- **Worker**: Reports to master URL, receives remote commands
- Configured in `bot_config.json` → `global` → `mode` and `host_settings`

### Key Modules

| Module | Location | Description |
|--------|----------|-------------|
| Device wrapper | `device_wrapper.py` | `MonitoredDevice` wraps adb/web backends |
| State tracking | `bot_state.py` | Per-device state, pause/skip flags, web launch requests |
| Wake-up handler | `utils/wake_up_handler.py` | Screen wake/ unlock, connection locking |
| OCR | `img_tools.py` | Multi-server fallback with circuit breaker |
| Lamp (開神燈) | `opengold_v2/` | 唯一 live 路徑：`game_actions/lamp_scheduler.py` → `opengold_v2.LampService`。V1 `Open_gold_paddle_ocr.py` 已廢棄 |
| Mining AI | `miner/` | screenshot → CNN classify → plan → execute；planner 預設 **v1**（A*），v1/v3/v4 可切（`mining_planner_version`）。分析見 [`docs/MINING_ALGORITHM_ANALYSIS.md`](docs/MINING_ALGORITHM_ANALYSIS.md) |
| Mining planner v3/v4 | `miner/v3,v4/` | v3 cluster-aware actions (230ms deadline)、v4 bounded 3-step DFS + branch-and-bound (250ms deadline)。深度追蹤（純 telemetry）：`miner/depth_tracker.py`（row-shift 偵測 + WS baseline 校準口） |
| OpenGold v2 | `opengold_v2/` | 神燈 refactor — split into 8 modules, central `OpenGoldConfig`, auto-detect 連閃裝備 |
| Farm v2 | `farm_v2/` | Farm-task refactor with state machine (`states.py`, `manager.py`, `operations/`) |
| Task sandbox | `task_sandbox/` | 通用任務開發/驗證框架，以神燈為第一個實作，基於 NavTarget 導航 |
| WS listener | `utils/ws_listener.py` | WebSocket frame 擷取與回放，用於協議分析 |
| WS-first 階段 | `game_actions/ws_phase.py` | 喚醒後、瀏覽器啟動前先跑純 WS 任務（`ws_token/runner.py`），成功項由 `daily_pipeline`（`ctx.ws_done`）跳過；ticket 由 Playwright 階段回寫（`utils/ws_ticket_refresh.py`）。裝置開關 `ws_token.enabled`（dashboard「方案」選擇器 adb/adb+ws/h5/h5+ws） |
| Equipment cache | `utils/equipment_cache.py` | 解析神燈掉落二進位資料，持久化並依 uid 查詢裝備 |
| Log paths | `utils/log_paths.py` | 集中管理 log 路徑；測試用 `LogPaths.with_root(tmp_path)` 沙箱 |
| Dashboard auth | `control_panel/shared/auth.py` + `utils/dashboard_settings.py` | 全站登入/帳號審核/裝置可見性/host_role 覆寫；設定檔 `dashboard_settings.json` 為 gitignored |

### Runtime Services (lazy-started)

| Service | Module | Purpose |
|---------|--------|----------|
| Push server | `runtime_services.push_server_service` | Real-time state push to dashboard |
| Device scanner | `runtime_services.device_scan_service` | Periodic ADB scan, device thread lifecycle |
| Worker sync | `runtime_services.worker_sync_service` | Worker→master state sync |
| Web session | `runtime_services.web_session_service` | Playwright session lifecycle, manual mode |

## Mining Module (`miner/`)

Planner default **v1** (whole-board A*); v3/v4 = bounded search. Shared mechanics:
- 7-row viewport, scroll-triggered when row 6 cleared
- Props: bomb (3x3 + cross), drill (vertical + bottom row)
- Cost model: pickaxe=1.0; v1 props=2.99; v4 drill=2.5 / bomb=3.5
- Dead-loop detection, auto-aborts after 3 identical states
- Real density ~3.6%; clusters 1x1/2x2/3x3; details in [`docs/MINING_ALGORITHM_ANALYSIS.md`](docs/MINING_ALGORITHM_ANALYSIS.md)

Key files:
- `miner/mining_service.py` - orchestrates screenshot → classify → plan → execute
- `miner/planning/smart_planner.py` - A* implementation
- `miner/models/classifier.py` - CNN block classifier
- `miner/core/mechanics.py` - prop effect calculations (source of truth)

### Miner planners v1 / v3 / v4 (wired; v1 is the default)

`mining_service.py` dispatches on `mining_planner_version` (default **v1**, see `config_manager.py` `DEFAULT_DEVICE_CONFIG`). Selectable per device:
- v1 (`miner/planning/smart_planner.py`, A*) — **current default** (2026-06-18)：whole-board A*，
  真實 3.6% 密度 eval score/省鏟最高（v1=3126 vs v4=1359；v5=1173 已移除）。
  ⚠ WS 挖礦 (`ws_token/mining_adapter.py`) 走 **v4**（非 v1）：v1 在無 pit + floor7 開時直接回空步，
  WS 監督迴圈需要 planner 持續吐 no_pit 進度挖步來捲動，故沿用 v4（v5 的骨架）。
- `miner/v4/` — bounded 3-step rolling-horizon DFS + branch-and-bound (250ms deadline)；reuses `core.mechanics` + `v3.actions`。
- `miner/v3/` — cluster-aware action model (`clusters`/`actions`/`board`); v4 reuses `v3.actions`. 有 230ms wall-clock deadline。

> v2/v5 已移除。`miner/v2/` 套件保留（CNN 分類層共用）；`depth_tracker.py` 保留為 telemetry。

Debug CLIs (run on a single screenshot)：
```bash
python -m miner.v2.debug_with_image <screenshot.png>            # 純分類 (共用 classifier)
python -m miner.v3.debug_with_image_plan <screenshot.png>       # v3 規劃
python -m miner.v2.debug_with_image_llm <screenshot.png> [--with-image]
```

## Configuration (`bot_config.json`)

### Per-device settings
```json
{
  "backend": "adb" | "web_h5",
  "enable_farm": true,
  "enable_mining": true,
  "lamp_check_interval": 2,
  "web_url": "https://...",  // for web_h5
  "web_state_file": "auth_state/{device_id}.json"
}
```

### Global settings
```json
{
  "mode": "master" | "worker",
  "master_url": "http://...",
  "ocr": {
    "servers": ["http://..."],
    "server_mode": "auto"
  }
}
```

### Host-specific overrides

`host_settings" → `YOUR-HOSTNAME` can override `mode`, `master_url`, `allow_web_backend`

## State Machine (per-device thread)

```
SCAN → WAKE_UP → CHECK_STAGE → [TASK LOOP] → SLEEP → (repeat)
```

Tasks executed in order:
1. 地獄之門 (Hell Gate) - daily dungeon
2. 農場任務 (Farm)
3. 寶箱 (Chest reward)
4. 家族任務 (Family)
5. 商店購買 (Shop)
6. 挖礦/Oracle (Mining AI)
7. 菇菇武道會 (Arena - periodic)
8. 航海任務 (Sea - periodic)
9. 萬神試煉 (Weekly dungeon, Mon-Sat only)
10. 開神燈 (OCR-based lamp, per `lamp_check_interval` hours)
11. 轉盤金幣 (Spin wheel)

## Logging

Per-device layout (Phase 01 reorg, 2026-05-02):

```
logs/
├── <device>/                          ← 一個裝置一個資料夾
│   ├── main.log                       ← 主任務迴圈
│   ├── miner.log                      ← 挖礦
│   ├── ocr_trace.log                  ← OCR 追蹤（512KB rotated）
│   ├── error_screenshots/             ← SmartScreenshotRecorder 產物
│   │   ├── events.jsonl
│   │   ├── annotations.json
│   │   └── *.jpg
│   └── action_trace/                  ← ActionTraceRecorder 產物
│       └── events_YYYYMMDD.jsonl
├── _archive/<device>/                 ← rotated 歷史檔（rotation 自動寫入）
│   └── main.20260502_143001.log
└── system/                            ← 預留：control panel / push server
```

- 路徑來源：`utils/log_paths.LogPaths`（測試用 `LogPaths.with_root(tmp_path)` 沙箱）
- Format: `%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s`
- 啟動時自動 rotate：active `main.log` → `<device>/main.YYYYMMDD_HHMMSS.log`
  - `_is_rotatable_active_log()` 過濾，跳過已 rotated 與 `sync-conflict-*`，避免 `name.t1.t2.t3.log` 檔名增生
  - 跳過 `_archive/`、`system/` 子目錄
- 自動 purge：每個 logger 啟動時清掉 ≥7 天前 rotated 副本（`_DEVICE_LOG_RETENTION_DAYS`），ocr_trace 5 天

## Common Operations

### Start the bot
```powershell
conda activate mushroom1
python new_main_v2.py
```

### Access control panel
```
http://127.0.0.1:5002
```
全站需登入；首次登入帳密由 env 遷移產生，總後台（帳號審核/裝置可見性/host_role）在 `/admin`。

### Check device state
```python
from bot_state import get_device_state
print(get_device_state("emulator-5554"))
```

### Force device rescan
```python
from bot_state import mark_refresh_needed
mark_refresh_needed()
```

### Run tests

```bash
# Default for Codex/agents: run only tests related to the files changed.
# Do not use bare `pytest` as the default in this repo; some tests import
# real device / Playwright / OpenCV / OCR dependencies and may hang or fail
# when the full runtime environment is not active.
python -m pytest tests/test_carpark_auto.py tests/test_game_initialization.py -q

# Syntax check for the same focused change set.
python -m py_compile utils/carpark_auto.py game_initialization.py tests/test_carpark_auto.py tests/test_game_initialization.py

# Single file / single test
python -m pytest tests/test_miner_depth_tracker.py -q
python -m pytest tests/test_miner_depth_tracker.py::test_name -q
```

Tests live in `tests/` with fixtures under `tests/fixtures/` and screenshot fixtures under `tests/images/`. Notable areas: miner v2 (`test_miner_v2_*`), MuMu watchdog (`test_mumu_*`), instance-flow guards, biweekly scheduler, OCR utils.

If pytest prints `.pytest_cache` permission warnings on the NAS path, ignore them unless the test result itself failed. If it reports `ModuleNotFoundError: cv2`, the command likely reached tests that import the real `device_wrapper`; narrow the command to the target test files or stub heavy imports inside that test.

### Standalone lamp (神燈) — 已廢棄

Runtime 一律走 `opengold_v2.LampService`（`game_actions/lamp_scheduler.py`）。V1 CLI `Open_gold_paddle_ocr.py` 僅供獨立除錯。

## OCR 架構

統一入口 `img_tools.py`，fallback 順序：配置的 OCR servers → 本地 paddle OCR → Labeler endpoint。
連續失敗啟用 circuit breaker 冷卻。

## Runtime Constraints

- **No `.pyc` writes**: `sys.dont_write_bytecode = True` is set early to avoid I/O stalls when the repo lives on SMB/NAS. Don't re-enable bytecode.
- **Model sync**: `utils/model_sync.ensure_local_model()` copies CNN weights to local SSD before load — required for NAS-hosted checkouts.
- **UTF-8 BOM**: Many JSON / `.py` files carry a BOM. When reading with `open()`, use `encoding="utf-8-sig"` or strip it — plain `utf-8` will leave a stray `\ufeff`.
- **Thread registry**: `_running_threads` in `new_main_v2.py` tracks every device thread. `Ctrl+C` fans out to `shutdown_web_devices()` before exit — don't bypass it.
- **Login conflicts**: `StartupLoginConflictError` / `LoginConflictError` force a 30-min device sleep. Treat as expected, not as a bug to catch-and-retry.
- **RL logs path**: Unified at `miner/rl_logs/<device>/events.jsonl` with rotation. The old `miner/rl/rl_logs/` path is deprecated — don't resurrect it.
- **`_WEB_DEVICE_LOCK` 必須是 RLock**：`close()` 的 `finally` 會重入此鎖，不可降級為 `Lock`，否則同 thread 會 deadlock。
- **Hot-reload 不存在**：改了 `utils/`、`game_actions/` 等模組後，正在跑的 bot 不會自動載入新程式碼；須重啟 `new_main_v2.py`（`sys.modules` cache）。

## H5 / Cocos 自動化慣例

- **Viewport 540x960 先行**：H5 session 開始時先 `set_viewport_size(540, 960)` — 否則所有固定座標偏移。
- **cc.Button 要 mouse.click**：Cocos `cc.Button` 必須用 Playwright `mouse.click(x,y)`，`emit('click')` 無效。`EditBox` 填值用 `editBox.string = '...'` 而非 Playwright type。
- **UIList Label 不可信**：Cocos UIList 回收 cell，Label 文字可能是舊的；判斷 cell 狀態用 sub-node `.active` 屬性。
- **測試裝置先 manual-hold**：對正在跑的裝置做 live-test 前，用 dashboard「開啟瀏覽器」取得 manual-hold 排他控制。
- **雙後端開發順序**：遊戲任務先 H5 開發驗證，再 ADB；u2 `send_keys` 在 H5 webview 無效，文字輸入改 `adb shell input text`。

## Scheduling Notes

- Wake times align to hourly 00~20 min window by default
- `emulator-5554` / `emulator-5560` 每小時醒來一次（±30 s）
- `emulator-5558` uses 1~3 h random interval
- `emulator-5554` handles cross-device online-check for `emulator-5558`
- `emulator-5556` runs biweekly bounty dungeon Sat/Sun 19:57
- `adjust_wake_time_for_cars()` shifts wake for 車位戰鬥
- Web H5 devices use Playwright Chrome channel with persisted profiles (`playwright_profile*/`)

## GSD Workflow

This repo uses the GSD planning structure under `.planning/` (`PROJECT.md`, `ROADMAP.md`, `REQUIREMENTS.md`, `STATE.md`, `phases/`, `todos/`, `codebase/`). Active phases live in `.planning/phases/NN-<slug>/`. When doing non-trivial work, prefer the `/gsd:*` commands (e.g. `gsd:progress`, `gsd:plan-phase`, `gsd:execute-phase`) over ad-hoc edits.

## Companion Docs

- `AGENTS.md` — GSD-oriented operations manual (startup checklist, FAQ, test commands)
- `PROJECT_OVERVIEW.md` / `SCRIPT_ARCHITECTURE.md` — conceptual layering (入口 → 裝置 → 辨識 → 任務 → 設定)
- `PROJECT_RUNBOOK.md` — maintenance principles (observability-first, cache vs real-time checks)
- `README_FLASK_SERVER.md` / `README_NEW_ARCHITECTURE.md` — deeper dives on dashboard and refactor
- `docs/EVENT_INDEX_DEV_GUIDE.md`, `docs/SMART_SCREENSHOT_LLM_ANALYZER.md` — subsystem guides

## Testing Conventions

### TDD Workflow
- Always write failing tests BEFORE implementation
- Use AAA pattern: Arrange-Act-Assert
- One assertion per test when possible
- Test names describe behavior: "should_return_empty_when_no_items"

### Test-First Rules
- When I ask for a feature, write tests first
- Tests should FAIL initially (no implementation exists)
- Only after tests are written, implement minimal code to pass

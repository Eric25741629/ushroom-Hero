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
| `control_panel_app.py` | Flask-based central control dashboard (port 5002) |
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
| Mining AI | `miner/` | screenshot → CNN classify → plan → execute；planner 預設 **v4**，v1/v3/v4 可切（config `mining_planner_version`）。v2 已移除 (2026-06-05，真實 board 18.8% 破 0.3s)。分析見 [`docs/MINING_ALGORITHM_ANALYSIS.md`](docs/MINING_ALGORITHM_ANALYSIS.md) |
| Mining planner v3/v4 | `miner/v3,v4/` | v3 cluster-aware actions (有 230ms deadline)、v4（預設）bounded 3-step DFS + branch-and-bound (250ms deadline) |
| OpenGold v2 | `opengold_v2/` | 神燈 refactor — split into 8 modules, central `OpenGoldConfig`, auto-detect 連閃裝備 |
| Farm v2 | `farm_v2/` | Farm-task refactor with state machine (`states.py`, `manager.py`, `operations/`) |
| Task sandbox | `task_sandbox/` | 通用任務開發/驗證框架，以神燈為第一個實作，基於 NavTarget 導航 |
| WS listener | `utils/ws_listener.py` | WebSocket frame 擷取與回放，用於協議分析 |
| Equipment cache | `utils/equipment_cache.py` | 解析神燈掉落二進位資料，持久化並依 uid 查詢裝備 |
| Log paths | `utils/log_paths.py` | 集中管理 log 路徑；測試用 `LogPaths.with_root(tmp_path)` 沙箱 |

### Runtime Services (lazy-started)

| Service | Module | Purpose |
|---------|--------|----------|
| Push server | `runtime_services.push_server_service` | Real-time state push to dashboard |
| Device scanner | `runtime_services.device_scan_service` | Periodic ADB scan, device thread lifecycle |
| Worker sync | `runtime_services.worker_sync_service` | Worker→master state sync |
| Web session | `runtime_services.web_session_service` | Playwright session lifecycle, manual mode |

## Mining Module (`miner/`)

Search-based automation (planner default **v4** = bounded DFS; v1 = A*). Shared mechanics:
- 7-row viewport, scroll-triggered when row 6 cleared
- Props: bomb (3x3 + cross), drill (vertical + bottom row)
- Cost model: pickaxe=1.0；v1 props=2.99；v4 rarity weights drill=2.5 / bomb=3.5 (源頭 `miner/v4/planner.py`)
- Dead-loop detection, auto-aborts after 3 identical states
- 真實 regime (礦脈**時間追蹤** `tools/track_pits_replay.py`)：cluster 是正方 1x1/2x2/3x3 (數量 66/18/17%，但 3x3 占 ~52% 礦格)；spawn 密度 ~3.6%；每回合 75% no_pit。⚠ 單張快照連通分量會**漏判 3x3** (跨 row 被逐步收集)。planner 比較與礦物出現率校正見 [`docs/MINING_ALGORITHM_ANALYSIS.md`](docs/MINING_ALGORITHM_ANALYSIS.md)

Key files:
- `miner/mining_service.py` - orchestrates screenshot → classify → plan → execute
- `miner/planning/smart_planner.py` - A* implementation
- `miner/models/classifier.py` - CNN block classifier
- `miner/core/mechanics.py` - prop effect calculations (source of truth)

### Miner planners v1 / v3 / v4 (wired; v4 is the default)

`mining_service.py` dispatches on `mining_planner_version` (default **v4**, see `config_manager.py` `DEFAULT_DEVICE_CONFIG`). Selectable per device:
- `miner/v3/` — cluster-aware action model (`clusters`/`actions`/`board`); v4 reuses `v3.actions`. 有 230ms wall-clock deadline。
- `miner/v4/` — **current default**: bounded 3-step rolling-horizon DFS + branch-and-bound (250ms deadline)；reuses `core.mechanics` + `v3.actions`。真實 board 最快 (mean 1.1ms / max 46ms)。
- v1 (`miner/planning/smart_planner.py`, A*) — 最省鏟、看得最遠的效率替代，`mining_planner_version='v1'` 可切。

> **v2 已移除** (2026-06-05)：真實 board 重放 18.8% 超過 0.3s、max 1841ms、歷史會 stuck。
> `miner/v2/` 套件保留，因 `classifier.py / service.py / types.py / visualization.py` 是 v3/v4
> 共用的 CNN 分類層；只刪了 `plan_v2` 演算法。

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
- 從舊 flat layout 遷移：先停 bot，再跑 `python tools/migrate_logs_layout.py --apply`（預設 dry-run；bot 在跑時 `--apply` 會被擋）

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
python -m pytest tests/test_miner_v2_planner.py -q
python -m pytest tests/test_miner_v2_planner.py::test_name -q
```

Tests live in `tests/` with fixtures under `tests/fixtures/` and screenshot fixtures under `tests/images/`. Notable areas: miner v2 (`test_miner_v2_*`), MuMu watchdog (`test_mumu_*`), instance-flow guards, biweekly scheduler, OCR utils.

If pytest prints `.pytest_cache` permission warnings on the NAS path, ignore them unless the test result itself failed. If it reports `ModuleNotFoundError: cv2`, the command likely reached tests that import the real `device_wrapper`; narrow the command to the target test files or stub heavy imports inside that test.

### Standalone lamp (神燈) entry

> 注意：runtime 開神燈一律走 `opengold_v2.LampService`（`game_actions/lamp_scheduler.py`）。下方 V1 CLI 已廢棄，僅保留作獨立除錯參考。

```bash
python Open_gold_paddle_ocr.py            # 連閃裝備模式預設啟用
python Open_gold_paddle_ocr.py --no-lian-shan
```

## OCR 架構

專案有兩套 OCR 使用情境：

| 情境 | 模組 | 說明 |
|------|------|------|
| 一般畫面辨識 | `img_tools.py` | 統一管理，支援多 server priority fallback |
| 開神燈 | `Open_gold_paddle_ocr.py` | 已改用 `img_tools` 共用 fallback 機制 |

**Fallback 順序**：
1. 配置的多台 OCR server (`bot_config.json` → `global.ocr.servers`)
2. 本地 paddle OCR
3. Labeler endpoint (AI 輔助辨識)

**Circuit Breaker**：連續失敗後啟用冷卻機制，避免重複失敗

## Runtime Constraints

- **No `.pyc` writes**: `sys.dont_write_bytecode = True` is set early to avoid I/O stalls when the repo lives on SMB/NAS. Don't re-enable bytecode.
- **Model sync**: `utils/model_sync.ensure_local_model()` copies CNN weights to local SSD before load — required for NAS-hosted checkouts.
- **UTF-8 BOM**: Many JSON / `.py` files carry a BOM. When reading with `open()`, use `encoding="utf-8-sig"` or strip it — plain `utf-8` will leave a stray `\ufeff`.
- **Thread registry**: `_running_threads` in `new_main_v2.py` tracks every device thread. `Ctrl+C` fans out to `shutdown_web_devices()` before exit — don't bypass it.
- **Login conflicts**: `StartupLoginConflictError` / `LoginConflictError` force a 30-min device sleep. Treat as expected, not as a bug to catch-and-retry.
- **RL logs path**: Unified at `miner/rl_logs/<device>/events.jsonl` with rotation. The old `miner/rl/rl_logs/` path is deprecated — don't resurrect it.
- **`_WEB_DEVICE_LOCK` 必須是 RLock**：`close()` 的 `finally` 會重入此鎖，不可降級為 `Lock`，否則同 thread 會 deadlock。

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

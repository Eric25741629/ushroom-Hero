# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

菇勇者全自動掛機 - A multi-device automation bot for a mobile H5 game. Supports two backends:
- `adb`: Direct device/emulator control via `uiautomator2`
- `web_h5`: Playwright-based browser automation for H5 game

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
| OCR (开神灯) | `Open_gold_paddle_ocr.py` | 神灯 OCR，已改用 `img_tools` 共用 fallback |
| Mining AI (v1) | `miner/` | A* planner, CNN classifier, RL logging (current runtime) |
| Mining AI (v2) | `miner/v2/` | Fresh rewrite — dry-run planner + classifier, switchable behind flag |
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

A* search-based automation with:
- 7-row viewport, scroll-triggered when row 6 cleared
- Props: bomb (3x3 + cross), drill (vertical + bottom row)
- Cost model: pickaxe=1.0, props=2.99 (use if saves ≥3 pickaxes)
- Dead-loop detection, auto-aborts after 3 identical states

Key files:
- `miner/mining_service.py` - orchestrates screenshot → classify → plan → execute
- `miner/planning/smart_planner.py` - A* implementation
- `miner/models/classifier.py` - CNN block classifier
- `miner/core/mechanics.py` - prop effect calculations (source of truth)

### Miner V2 (experimental, not wired into runtime)

Rewrite under `miner/v2/`: new top-level strategy `has_pit` vs `no_pit`, bombs/drill as first-class search actions (not side evaluators), `dug_pit` treated as air. Classifier + dry-run planner only — no executor. Switchable in runtime via feature flag (see `a36c505` commit), but defaults off.

Debug CLIs (run on a single screenshot):
```bash
python -m miner.v2.debug_with_image <screenshot.png>
python -m miner.v2.debug_with_image_plan <screenshot.png> --shovels 100 --drill 1 --bomb 1
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
# Full suite (conftest.py adds repo root to sys.path)
pytest

# Single file / single test
pytest tests/test_miner_v2_planner.py
pytest tests/test_miner_v2_planner.py::test_name -v
```

Tests live in `tests/` with fixtures under `tests/fixtures/` and screenshot fixtures under `tests/images/`. Notable areas: miner v2 (`test_miner_v2_*`), MuMu watchdog (`test_mumu_*`), instance-flow guards, biweekly scheduler, OCR utils.

### Standalone lamp (神燈) entry

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
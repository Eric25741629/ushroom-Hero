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
| Mining AI | `miner/` | A* planner, CNN classifier, RL logging |

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

- Main: `logs/{ip}.log`
- Mining: `logs/miner_{ip}.log`
- Format: `%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] %(message)s`
- Auto-rotated on startup (old → `.bak`)

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

## OCR 架構

專案有兩套 OCR 使用情境：

| 情境 | 模組 | 說明 |
|------|------|------|
| 一般畫面辨識 | `img_tools.py` | 統一管理，支援多 server priority fallback |
| 開神燈/點金 | `Open_gold_paddle_ocr.py` | 已改用 `img_tools` 共用 fallback 機制 |

**Fallback 順序**：
1. 配置的多台 OCR server (`bot_config.json` → `global.ocr.servers`)
2. 本地 paddle OCR
3. Labeler endpoint (AI 輔助辨識)

**Circuit Breaker**：連續失敗後啟用冷卻機制，避免重複失敗

## Notes

- Wake times align to hourly 00~20 min window by default
- `7fe98fc6` device wakes every hour (special case)
- `emulator-5554` handles cross-device online-check for `emulator-5558`
- Web H5 devices use Playwright Chrome channel with persisted profiles

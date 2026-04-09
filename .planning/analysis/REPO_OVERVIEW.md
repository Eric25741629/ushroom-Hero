# Repo Overview - 菇勇者全自動掛機

## Project Summary

這是一個針對「菇勇者」手遊的全自動掛機系統，支援多裝置（模擬器 + 實體手機）、多實例並行運行。

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    Control Panel (Flask)                 │
│              http://127.0.0.1:5002                       │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        ▼                         ▼
┌──────────────┐          ┌──────────────┐
│   Master     │◄────────►│   Worker     │
│  (Local)     │  Push    │  (Remote)    │
└──────┬───────┘          └──────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│                  Device Layer                        │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐        │
│  │emulator│ │emulator│ │emulator│ │  Phone  │        │
│  │5554    │ │5556    │ │5558/60 │ │7fe98fc6 │        │
│  └────────┘ └────────┘ └────────┘ └────────┘        │
└──────────────────────────────────────────────────────┘
```

---

## Key Components

### Core Files
| File | Purpose |
|------|--------|
| `new_main_v2.py` | Main entrypoint, device loop coordinator |
| `bot_state.py` | Per-device state tracking |
| `config_manager.py` | Configuration management |
| `control_panel_app.py` | Flask web control panel backend |

### Game Automation
| Module | Purpose |
|--------|--------|
| `game_actions/` | Daily tasks, rewards, skill switching |
| `game_state/detector.py` | Stage detection via image matching |
| `game_initialization.py` | Startup flow, login handling |

### Mining AI (`miner/`)
| Component | Technology |
|-----------|------------|
| Classifier | CNN (PyTorch) - board classification |
| Planner | A* search with multi-step lookahead |
| Executor | Converts plans to ADB clicks |
| RL Recorder | Logs for reinforcement learning |

### OCR System
- **Primary**: PaddleOCR (`Open_gold_paddle_ocr.py`)
- **Fallback**: Shared fallback mechanism across services
- **Unified Interface**: `img_tools.py`

---

## Technical Stack

```
┌─────────────────────────────────────────┐
│  Language: Python 3.x                   │
├─────────────────────────────────────────┤
│  Device Control:                        │
│    • ADB + uiautomator2 (real devices)  │
│    • Playwright (Web H5 mode)           │
├─────────────────────────────────────────┤
│  AI/ML:                                 │
│    • PyTorch (CNN classifiers)          │
│    • A* pathfinding                     │
│    • RL components (SB3/PPO)            │
├─────────────────────────────────────────┤
│  OCR: PaddleOCR, EasyOCR                │
├─────────────────────────────────────────┤
│  Web: Flask + Jinja2 templates          │
├─────────────────────────────────────────┤
│  Image Processing: OpenCV               │
└─────────────────────────────────────────┘
```

---

## Current State Assessment

### ✅ Completed/Stable
- Multi-device automation foundation
- Mining AI with CNN + A* planner
- Web control panel (basic)
- Master/Worker architecture
- Phase 1: MuMu emulator management
- Phase 4: Bi-weekly dungeon automation

### ⚠️ Needs Improvement
1. **State Machine**: No unified FSM for device states
2. **Stability**: Limited auto-recovery capabilities
3. **Observability**: State transitions not fully tracked
4. **Scheduler**: Task prioritization could be improved
5. **Web UI**: Real-time control features incomplete

---

## File Statistics

```
Total Python files: ~100+
Core modules:
  • game_actions/: 6 files
  • miner/: 30+ files (AI/ML)
  • utils/: 10+ files
  • runtime_services/: 5 files
```

---

## Notable Features

1. **Wake-up Alignment**: Devices wake at staggered times to avoid network congestion
2. **Dead Loop Detection**: Mining AI aborts after 3 consecutive identical states
3. **SMB/NAS Optimization**: `sys.dont_write_bytecode = True` prevents I/O lag
4. **Model Sync**: Ensures ML models are on local SSD for performance
5. **Login Conflict Handling**: Auto-sleep 30min on异地登录 detection

---

## Dependencies on External Services

- OCR servers (configurable in `bot_config.json`)
- Push server for Master/Worker communication
- ADB servers for device control

---

*Generated: Analysis of existing codebase structure*

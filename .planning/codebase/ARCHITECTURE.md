# Architecture

Updated: 2026-03-13  
Repository root: `A:\菇勇者全自動掛機`

## System Pattern
- The project follows a script-orchestrated automation pattern centered on `new_main_v2.py`.
- Runtime behavior is a long-lived control loop per device, not a single request/response app.
- It combines local device control (ADB/uiautomator2), OCR/vision inference, and task dispatch.
- A secondary control-plane pattern exists through Flask endpoints in `control_panel_app.py`.
- State is persisted as JSON + logs rather than a relational service as primary source of truth.

## Layers And Modules
- Orchestration layer: `new_main_v2.py`, `game_initialization.py`, `event_manager.py`.
- Device/IO layer: `adb_operations.py`, `device.py`, `device_wrapper.py`, `utils/wake_up_handler.py`.
- Perception layer: `img_tools.py`, `game_state/detector.py`, `new_cnn/cnn_model.py`, `ocr_server.py`.
- Action layer: `game_actions/daily_tasks.py`, `game_actions/miner_action.py`, `game_actions/reward_manager.py`, `game_actions/periodic_tasks.py`, `game_actions/skill_manager.py`.
- Domain modules: `miner/`, `farm/`, `mission/`, `family/`, `park.py`, `fight_car.py`.
- State/config layer: `bot_state.py`, `config_manager.py`, `json_manager.py`, `bot_config.json`, `emulator-5554.json` (and similar per-device JSON files).
- Observability/support: `utils/logging_utils.py`, `logs/`, `miner/rl_logs/`, `docs/INDEX.md`.
- UI/control endpoints: `control_panel_app.py`, `app.py`, `serve.py`, plus static pages such as `菇勇者.html` and `templates/`.

## Data Flow
1. Startup loads models/config from `new_main_v2.py` using `config_manager.py`, `utils/model_loader.py`, and CNN model files like `cnn_model.pth`.
2. Device connection and readiness checks are performed through `adb_operations.py` and wrapped via `device_wrapper.py`.
3. Screenshots are captured (`d.screenshot`) and routed to OCR/CNN logic in `img_tools.py`, `game_state/detector.py`, and `new_cnn/cnn_model.py`.
4. Stage classification output drives action routing into modules under `game_actions/` and feature modules (`mission`, `farm`, `family`, `miner`).
5. Mining branch calls `miner/mining_service.py`, which further uses `miner/core/`, `miner/planning/`, and `miner/models/`.
6. State transitions and pause/refresh commands are synchronized via `bot_state.py` and dashboard APIs in `control_panel_app.py`.
7. Task timestamps/cooldowns are read/written by `json_manager.py` to per-device JSON records.
8. Runtime and debug outputs are written to `logs/`, `easyocr_calls.log`, and RL traces in `miner/rl_logs/<device>/events.jsonl`.

## Entry Points
- Primary runtime entry: `new_main_v2.py`.
- Control panel web service: `control_panel_app.py` (Flask routes under `/api/...` and dashboard page).
- Static/local API server: `app.py` (Flask) and `serve.py` (simple HTTP + CORS).
- OCR service process: `ocr_server.py` (standalone OCR endpoint host).
- Test entry area: `tests/test_smoke_config_api.py` and module-level test scripts like `test_json_manager.py`.

## Notable Boundaries
- `miner/` is a semi-independent subsystem with its own `core/`, `planning/`, `models/`, `rl/`, and `scripts/`.
- `game_actions/` is intentionally thin command logic that depends on detector/state outputs.
- `bot_state.py` acts as shared in-memory coordination and worker/master sync boundary.
- JSON files (for example `bot_config.json` and `emulator-5560.json`) are effectively configuration/state contracts across scripts.

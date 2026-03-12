# Structure

Updated: 2026-03-13  
Repository root: `A:\菇勇者全自動掛機`

## Directory Layout
- `miner/`: mining subsystem with `core/`, `planning/`, `models/`, `rl/`, `scripts/`, `dataset/`, `rl_logs/`.
- `game_actions/`: action executors such as `daily_tasks.py`, `periodic_tasks.py`, `reward_manager.py`, `miner_action.py`.
- `game_state/`: stage detection (`detector.py`) used by orchestration and action layers.
- `utils/`: cross-cutting helpers (`logging_utils.py`, `model_loader.py`, `ocr_clicker.py`, `wake_up_handler.py`).
- `tests/`: pytest-style tests (for example `tests/test_smoke_config_api.py`) and cached bytecode under `tests/__pycache__/`.
- `docs/`: documentation index and notes (`docs/INDEX.md`).
- `config/`: configuration assets used by runtime scripts.
- `templates/`: HTML template resources for panel/UI components.
- `logs/`: runtime log outputs.
- `new_cnn/`: CNN model code and related inference support.
- `everyday_mission/`, `farm/`, `family/`, `mission/`, `oracle/`, `partner/`: feature-specific automation modules/data.

## Key Root Files
- Main orchestration: `new_main_v2.py`.
- Web control panel: `control_panel_app.py`.
- Local flask/static service: `app.py`.
- Static file server utility: `serve.py`.
- Device and ADB access: `device.py`, `device_wrapper.py`, `adb_operations.py`, `adb_devices.py`.
- State/config: `bot_state.py`, `config_manager.py`, `json_manager.py`, `bot_config.json`.
- OCR/inference: `img_tools.py`, `ocr_server.py`, `cnn_model.py`, `cnn_model.pth`.
- Project docs: `PROJECT_OVERVIEW.md`, `PROJECT_RUNBOOK.md`, `SCRIPT_ARCHITECTURE.md`.

## Naming Conventions Observed
- Predominant Python module naming is `snake_case.py` (for example `game_initialization.py`, `daily_gift_task.py`).
- Some legacy/class-style filenames use PascalCase or uppercase-leading names: `Mission.py`, `Skill.py`, `Store.py`, `BUY.py`.
- Feature folders generally use lowercase snake case (`game_actions/`, `game_state/`, `new_cnn/`, `everyday_mission/`).
- Device state files follow `<device-id>.json` pattern such as `emulator-5554.json`, `emulator-5560.json`, `7fe98fc6.json`.
- Backup files append `.backup_<timestamp>` (for example `emulator-5554.json.backup_1770242849`).
- Logs and artifacts use descriptive suffixes like `_debug`, `_test`, `_analysis`, and date/time-like prefixes in some datasets.
- Python cache directories consistently use `__pycache__/` across root and submodules.

## Practical Navigation Rules
- Start runtime analysis from `new_main_v2.py`, then follow imports into `game_actions/`, `game_state/`, and `utils/`.
- Treat `miner/` as a dedicated bounded context; inspect `miner/mining_service.py` before deeper `miner/core/` or `miner/planning/` files.
- Treat root-level one-off scripts (`test_*.py`, `quick_test.py`, `update_config.py`) as utilities rather than core architecture.
- Treat `docs/` and `PROJECT_*.md` as operational context, not executable sources.
- Keep edits scoped carefully because root contains both active code and archival experiments (`new_main_before20250514.py`, notebook files, debug images).

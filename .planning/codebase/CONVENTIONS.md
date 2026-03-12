# Repository Conventions

## Scope
- This document summarizes patterns observed in active Python modules, especially `config_manager.py`, `control_panel_app.py`, `bot_state.py`, `game_initialization.py`, `utils/logging_utils.py`, and `app.py`.

## Style
- Language is Python, with modules organized mostly as flat top-level scripts plus feature folders like `game_actions/`, `game_state/`, `miner/`, and `utils/`.
- Imports are generally grouped as: standard library, third-party, then local modules (example in `control_panel_app.py`).
- Type hints are used inconsistently but present in core state/config code (for example `bot_state.py` and `config_manager.py`).
- JSON persistence uses explicit UTF-8 and pretty output (`json.dump(..., ensure_ascii=False, indent=4)`) in `config_manager.py`.
- Logging is preferred over raising for non-fatal operational issues; see `utils/logging_utils.py` and many `print(...)` status messages in `bot_state.py`.

## Naming
- File/module naming is snake_case for most Python modules: `config_manager.py`, `game_initialization.py`, `daily_gift_task.py`.
- Function names are snake_case and action-oriented: `load_config`, `update_device_config`, `get_all_states`, `set_pause`.
- Constants are UPPER_SNAKE_CASE in config/state modules (`CONFIG_FILE`, `DEFAULT_DEVICE_CONFIG`, `OFFLINE_RETENTION_SEC`).
- Internal shared state uses leading underscore names: `_states`, `_locks`, `_worker_queue`, `_cached_models`.
- API routes use `/api/...` prefix consistently in Flask apps (`control_panel_app.py`, `app.py`).

## Common Patterns
- Configuration merge/migration pattern:
- Load stored JSON, merge missing keys from defaults, and rewrite file when schema changed (`config_manager.py`).
- Defensive input normalization pattern:
- Convert incoming values, clamp numeric ranges, coerce booleans/strings (`update_device_config` and `update_ocr_config` in `config_manager.py`).
- Device-id normalization pattern:
- For remote IDs containing `:`, split and keep last segment before config lookup (`control_panel_app.py` route handlers).
- Thread-safe global state pattern:
- Use per-device lock + global lock + worker queue (`bot_state.py`).
- Background maintenance pattern:
- Daemon threads for cleanup/sync loops (`_housekeeper_loop`, `_worker_sync_loop` in `bot_state.py`).
- Logger-per-device pattern:
- Build sanitized log filenames and rotating handlers in `utils/logging_utils.py` writing to `logs/`.

## Error Handling
- API endpoints usually wrap bodies in `try/except` and return JSON with `{"status": "error", "message": str(e)}` and HTTP 500 in `control_panel_app.py`.
- Operational retries/fallbacks are common for network/device operations:
- Example: OCR health checks iterate multiple endpoints and continue on exceptions (`check_ocr_server` in `control_panel_app.py`).
- Fail-soft behavior is common in loops/services:
- Catch broad exceptions, log/print, then continue or reinitialize (`bot_state.py`, `game_initialization.py`).
- Config-loading fallback returns safe defaults on read/parse failure (`load_config` in `config_manager.py`).
- Explicit validation with min/max guards is preferred over exceptions for bad user input (`update_device_config`, `update_ocr_config` in `config_manager.py`).

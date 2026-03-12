# INTEGRATIONS

## External APIs and Network Endpoints
- Time API integration in `app.py` via outbound HTTP calls to:
- `https://worldtimeapi.org/api/timezone/Asia/Taipei`
- `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Taipei`
- `http://worldclockapi.com/api/json/utc/now`
- OCR service endpoints are consumed by application code via HTTP in `img_tools.py` and `control_panel_app.py`.
- Default OCR targets include `http://100.64.0.5:5001`, `http://100.64.0.7:5001`, and `http://localhost:5001` (see `img_tools.py`, `config_manager.py`).
- Worker-to-master control-plane integration uses HTTP in `bot_state.py` against configured `master_url` (default `http://127.0.0.1:5002`).
- Control panel performs OCR server health probes via `GET /health` in `control_panel_app.py`.

## Device / Platform Integrations
- Android Debug Bridge (ADB) is a first-class external integration for emulator/device control:
- Command execution wrappers in `adb_operations.py` and `adb_devices.py`.
- Explicit server restart calls (`adb kill-server`, `adb start-server`) in `control_panel_app.py`.
- Android UI automation provider is `uiautomator2` across core automation flows (`new_main_v2.py`, `device.py`, `new_battle.py`, `family.py`).

## OCR / ML Service Integrations
- External OCR runtime SDK: `paddleocr.PaddleOCR` in `ocr_server.py`.
- Legacy/local OCR SDK: `easyocr` in `new_main_v2.py`, `fight_car.py`, `new_battle.py`.
- Vision/ML stack integrates `torch` models from disk (`cnn_model.pth`) loaded in `new_main_v2.py`, `control_panel_app.py`, `cnn_model.py`.
- CV processing SDK integration: OpenCV (`cv2`) and NumPy in `img_tools.py`, `fight_car.py`, `Mission.py`.

## Database and Storage Integrations
- No ORM-backed relational DB integration found in primary runtime code paths.
- File-backed JSON storage is heavily integrated for bot state and schedules:
- Config loader/saver in `config_manager.py` for `bot_config.json`.
- Device state persistence in `json_manager.py` with files like `emulator-5554.json`.
- SQLite storage appears in repository as `scan_results.sqlite` (plus `scan_results.sqlite-wal` and `scan_results.sqlite-shm`), used as local artifact storage rather than a declared service DB.
- Pickle-based persistence is used for RL/model artifacts (`miner_q_table.pkl`).

## Auth Providers and Access Control
- No OAuth/OIDC/JWT identity provider integration detected in main application services.
- OCR service applies network-based allowlisting in `ocr_server.py` using `ipaddress` and `ALLOWED_NETWORK` (`100.64.0.0/24`) plus loopback allowance.
- Control channel includes lightweight worker token/header pattern in `bot_state.py` (`X-Worker-Token`) but not a third-party auth provider.

## Webhooks, Callbacks, and Push/Pull Patterns
- No inbound third-party webhook provider (e.g. Stripe/GitHub/Discord webhook) is detected.
- Internal callback/polling pattern exists:
- Workers POST status to master in `bot_state.py` (`/api/report_status`).
- Workers poll command queue in `bot_state.py` (`/api/poll_commands`).
- Control panel exposes command/state APIs in `control_panel_app.py` for local orchestration, not public webhook consumption.

## Third-Party SDK Summary (Observed in Code)
- `flask` / `flask_cors`: service hosting and browser access (`app.py`, `control_panel_app.py`, `game_api.py`).
- `requests`: outbound HTTP integration (`img_tools.py`, `control_panel_app.py`, `bot_state.py`).
- `uiautomator2`: Android automation integration (`adb_operations.py`, `new_main_v2.py`, `device.py`).
- `paddleocr` and `easyocr`: OCR integrations (`ocr_server.py`, `new_main_v2.py`).
- `torch`, `numpy`, `cv2`: ML/CV runtime integrations (`cnn_model.py`, `img_tools.py`, `fight_car.py`).

## Notable Gaps / Operational Notes
- Dependency declarations in `requirements.txt` only cover Flask/CORS; many integrated SDKs are implicit imports and should be captured in a fuller lockfile.
- Integration endpoints and network topology are partly hardcoded in `config_manager.py` and `img_tools.py`; host-specific overrides are supported via `bot_config.json`.

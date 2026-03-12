# STACK

## Languages
- Primary language is Python across automation and services, e.g. `new_main_v2.py`, `control_panel_app.py`, `ocr_server.py`, `img_tools.py`.
- Markdown documentation is present for operations/design, e.g. `PROJECT_RUNBOOK.md`, `README_NEW_ARCHITECTURE.md`, `SCRIPT_ARCHITECTURE.md`.
- HTML frontend assets are served by Flask for local dashboards, e.g. `菇勇者.html`, `templates/dashboard.html`, `miner_visualizer.html`.
- JSON is the dominant runtime config/state format, e.g. `bot_config.json`, `emulator-5554.json`, `car_fight.json`, `manifest.json`.

## Runtime and Execution Model
- Runtime is CPython (project scripts are plain `.py` modules with `python <file>.py` entry style), e.g. `new_main_v2.py`, `app.py`, `serve.py`.
- Multi-process / multi-device orchestration is done at app level, with per-device loops and remote command polling in `new_main_v2.py` and `bot_state.py`.
- Local web services run on Flask:
- Lightweight API/static server in `app.py` (default `127.0.0.1:5000`).
- Control panel API/UI in `control_panel_app.py` (dashboard and orchestration endpoints).
- OCR service in `ocr_server.py` (health + OCR endpoints).
- Optional builtin HTTP server fallback exists in `serve.py` (`http.server` with CORS headers).

## Frameworks and Core Libraries
- Web framework: Flask (`app.py`, `control_panel_app.py`, `ocr_server.py`, `game_api.py`).
- CORS support: `flask-cors` (optional import path in `app.py` with fallback manual headers).
- Device automation: `uiautomator2` and shell ADB usage (`adb_operations.py`, `device.py`, `new_main_v2.py`).
- Computer vision: OpenCV (`cv2`) and NumPy for image processing (`img_tools.py`, `fight_car.py`, `family.py`).
- OCR stack:
- `PaddleOCR` server-side OCR in `ocr_server.py`.
- `easyocr` still used in game-logic flows in `new_main_v2.py`, `new_battle.py`, `fight_car.py`.
- ML inference: PyTorch models (`cnn_model.py`, `new_cnn/cnn_model.py`, model file `cnn_model.pth`).
- HTTP client: `requests` and `urllib.request` (`control_panel_app.py`, `img_tools.py`, `app.py`, `bot_state.py`).

## Dependencies and Packaging
- Declared pip dependencies are minimal in `requirements.txt`:
- `Flask>=2.0`
- `flask-cors>=3.0`
- Additional runtime dependencies are imported directly in code but not pinned centrally, including `requests`, `numpy`, `opencv-python` (`cv2`), `torch`, `paddleocr`, `easyocr`, `uiautomator2`.
- There is no detected `pyproject.toml`, `Pipfile`, or Poetry lock in repository root; dependency management is script-driven.

## Configuration and Environment
- Main bot config and host overrides are managed in `bot_config.json` via loader/merging logic in `config_manager.py`.
- OCR server routing and failover preferences are configured in `config_manager.py` (`global.ocr.servers`, `server_mode`).
- OCR service behavior reads env vars in `ocr_server.py`, e.g. `MAX_OCR_FAIL_IMAGES`, `MIN_OCR_FAIL_SCORE`, `MAX_OCR_FAIL_SCORE`, `IMG_DECODE_RETRIES`, `OCR_EMPTY_RETRIES`, `OCR_RETRY_DELAY`.
- Security/logging defaults for OCR service are centralized in `ocr_server_config.py`.
- Per-device state is persisted as device-specific JSON files, e.g. `emulator-5554.json`, `emulator-5556.json`, `7fe98fc6.json`.

## Data and Artifact Storage (Runtime)
- JSON state/config artifacts are primary store: `bot_config.json`, `car_fight.json`, `emulator-*.json`.
- SQLite appears as local analysis storage in `scan_results.sqlite` (+ WAL/SHM sidecars).
- ML assets are file-based: `cnn_model.pth`, `miner_q_table.pkl`, `dataset/`, `oracle/`.
- Operational logs/artifacts are file-based folders: `logs/`, `ocr_fails_new/`, `ocr_errors/`, `debug_img/`.

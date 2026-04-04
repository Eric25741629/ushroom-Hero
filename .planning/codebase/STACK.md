# STACK

## 語言
- 主要語言為 Python，涵蓋自動化與服務，例如 `new_main_v2.py`、`control_panel_app.py`、`ocr_server.py`、`img_tools.py`。
- 存在用於操作/設計的 Markdown 文件，例如 `PROJECT_RUNBOOK.md`、`README_NEW_ARCHITECTURE.md`、`SCRIPT_ARCHITECTURE.md`。
- 由 Flask 提供的 HTML 前端資產用於本機儀表板，例如 `菇勇者.html`、`templates/dashboard.html`、`miner_visualizer.html`。
- JSON 是主要的執行期設定/狀態格式，例如 `bot_config.json`、`emulator-5554.json`、`car_fight.json`、`manifest.json`。

## 執行環境與執行模型
- 執行環境為 CPython（專案腳本為純 `.py` 模組，採 `python <file>.py` 進入點風格），例如 `new_main_v2.py`、`app.py`、`serve.py`。
- 多程序 / 多裝置協調在應用層完成，包含每裝置迴圈與遠端指令輪詢，位於 `new_main_v2.py` 與 `bot_state.py`。
- 本機 Web 服務運行於 Flask：
- `app.py` 中的輕量 API/靜態伺服器（預設 `127.0.0.1:5000`）。
- `control_panel_app.py` 中的控制面板 API/UI（儀表板與協調端點）。
- `ocr_server.py` 中的 OCR 服務（健康檢查 + OCR 端點）。
- `serve.py` 中存在可選的內建 HTTP 伺服器備援（含 CORS 標頭的 `http.server`）。

## 框架與核心函式庫
- Web 框架：Flask（`app.py`、`control_panel_app.py`、`ocr_server.py`、`game_api.py`）。
- CORS 支援：`flask-cors`（`app.py` 中可選匯入路徑，並有手動標頭備援）。
- 裝置自動化：`uiautomator2` 與 shell ADB 使用（`adb_operations.py`、`device.py`、`new_main_v2.py`）。
- 電腦視覺：OpenCV（`cv2`）與 NumPy 用於影像處理（`img_tools.py`、`fight_car.py`、`family.py`）。
- OCR 技術棧：
- `ocr_server.py` 中以 `PaddleOCR` 執行伺服器端 OCR。
- `new_main_v2.py`、`new_battle.py`、`fight_car.py` 的遊戲邏輯流程中仍使用 `easyocr`。
- ML 推論：PyTorch 模型（`cnn_model.py`、`new_cnn/cnn_model.py`、模型檔 `cnn_model.pth`）。
- HTTP 用戶端：`requests` 與 `urllib.request`（`control_panel_app.py`、`img_tools.py`、`app.py`、`bot_state.py`）。

## 相依套件與封裝
- `requirements.txt` 中宣告的 pip 相依最少：
- `Flask>=2.0`
- `flask-cors>=3.0`
- 其他執行期相依直接在程式碼中匯入，且未集中釘版，包含 `requests`、`numpy`、`opencv-python`（`cv2`）、`torch`、`paddleocr`、`easyocr`、`uiautomator2`。
- 在儲存庫根目錄未偵測到 `pyproject.toml`、`Pipfile` 或 Poetry lock；相依管理偏向腳本驅動。

## 設定與環境
- 主要 bot 設定與主機覆寫透過 `bot_config.json` 管理，並由 `config_manager.py` 的載入/合併邏輯處理。
- OCR 伺服器路由與故障切換偏好在 `config_manager.py` 中設定（`global.ocr.servers`、`server_mode`）。
- OCR 服務行為在 `ocr_server.py` 讀取環境變數，例如 `MAX_OCR_FAIL_IMAGES`、`MIN_OCR_FAIL_SCORE`、`MAX_OCR_FAIL_SCORE`、`IMG_DECODE_RETRIES`、`OCR_EMPTY_RETRIES`、`OCR_RETRY_DELAY`。
- OCR 服務的安全性/日誌預設集中在 `ocr_server_config.py`。
- 每裝置狀態持久化為裝置專屬 JSON 檔，例如 `emulator-5554.json`、`emulator-5556.json`、`7fe98fc6.json`。

## 資料與產物儲存（執行期）
- 主要儲存為 JSON 狀態/設定產物：`bot_config.json`、`car_fight.json`、`emulator-*.json`。
- `scan_results.sqlite`（含 WAL/SHM 附屬檔）顯示 SQLite 作為本機分析儲存。
- ML 資產採檔案型態：`cnn_model.pth`、`miner_q_table.pkl`、`dataset/`、`oracle/`。
- 操作日誌/產物為檔案型資料夾：`logs/`、`ocr_fails_new/`、`ocr_errors/`、`debug_img/`。

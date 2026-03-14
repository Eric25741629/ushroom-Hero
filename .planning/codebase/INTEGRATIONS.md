# INTEGRATIONS

## 外部 API 與網路端點
- `app.py` 透過對外 HTTP 呼叫整合 Time API：
- `https://worldtimeapi.org/api/timezone/Asia/Taipei`
- `https://timeapi.io/api/Time/current/zone?timeZone=Asia/Taipei`
- `http://worldclockapi.com/api/json/utc/now`
- 應用程式碼在 `img_tools.py` 與 `control_panel_app.py` 透過 HTTP 使用 OCR 服務端點。
- 預設 OCR 目標包含 `http://100.64.0.5:5001`、`http://100.64.0.7:5001` 與 `http://localhost:5001`（見 `img_tools.py`、`config_manager.py`）。
- worker-to-master 控制平面整合在 `bot_state.py` 中使用 HTTP 對接設定的 `master_url`（預設 `http://127.0.0.1:5002`）。
- 控制面板在 `control_panel_app.py` 透過 `GET /health` 進行 OCR 伺服器健康探測。

## 裝置 / 平台整合
- Android Debug Bridge（ADB）是模擬器/裝置控制的一級外部整合：
- 指令執行封裝位於 `adb_operations.py` 與 `adb_devices.py`。
- `control_panel_app.py` 中有明確的伺服器重啟呼叫（`adb kill-server`、`adb start-server`）。
- Android UI 自動化提供者為 `uiautomator2`，涵蓋核心自動化流程（`new_main_v2.py`、`device.py`、`new_battle.py`、`family.py`）。

## OCR / ML 服務整合
- 外部 OCR 執行期 SDK：`ocr_server.py` 中的 `paddleocr.PaddleOCR`。
- 舊版/本機 OCR SDK：`new_main_v2.py`、`fight_car.py`、`new_battle.py` 中的 `easyocr`。
- 視覺/ML 技術棧整合磁碟載入的 `torch` 模型（`cnn_model.pth`），由 `new_main_v2.py`、`control_panel_app.py`、`cnn_model.py` 載入。
- CV 處理 SDK 整合：`img_tools.py`、`fight_car.py`、`Mission.py` 中的 OpenCV（`cv2`）與 NumPy。

## 資料庫與儲存整合
- 在主要執行路徑中未發現以 ORM 為基礎的關聯式資料庫整合。
- 檔案型 JSON 儲存高度整合於 bot 狀態與排程：
- `config_manager.py` 中 `bot_config.json` 的設定載入/儲存。
- `json_manager.py` 中裝置狀態持久化，檔案如 `emulator-5554.json`。
- SQLite 儲存在儲存庫中表現為 `scan_results.sqlite`（另含 `scan_results.sqlite-wal` 與 `scan_results.sqlite-shm`），用途較偏本機產物儲存，而非宣告式服務資料庫。
- 以 Pickle 為基礎的持久化用於 RL/模型產物（`miner_q_table.pkl`）。

## 驗證提供者與存取控制
- 在主要應用服務中未偵測到 OAuth/OIDC/JWT 身分提供者整合。
- OCR 服務在 `ocr_server.py` 使用 `ipaddress` 與 `ALLOWED_NETWORK`（`100.64.0.0/24`）加上 loopback 允許，實作基於網路的 allowlisting。
- 控制通道在 `bot_state.py` 含輕量 worker token/header 模式（`X-Worker-Token`），但非第三方驗證提供者。

## Webhook、回呼與 Push/Pull 模式
- 未偵測到第三方入站 webhook 提供者（例如 Stripe/GitHub/Discord webhook）。
- 存在內部回呼/輪詢模式：
- workers 在 `bot_state.py` 將狀態 POST 給 master（`/api/report_status`）。
- workers 在 `bot_state.py` 輪詢指令佇列（`/api/poll_commands`）。
- 控制面板在 `control_panel_app.py` 暴露本機協調用途的指令/狀態 API，而非公網 webhook 消費。

## 第三方 SDK 摘要（程式碼觀察）
- `flask` / `flask_cors`：服務託管與瀏覽器存取（`app.py`、`control_panel_app.py`、`game_api.py`）。
- `requests`：對外 HTTP 整合（`img_tools.py`、`control_panel_app.py`、`bot_state.py`）。
- `uiautomator2`：Android 自動化整合（`adb_operations.py`、`new_main_v2.py`、`device.py`）。
- `paddleocr` 與 `easyocr`：OCR 整合（`ocr_server.py`、`new_main_v2.py`）。
- `torch`、`numpy`、`cv2`：ML/CV 執行期整合（`cnn_model.py`、`img_tools.py`、`fight_car.py`）。

## 顯著缺口 / 營運備註
- `requirements.txt` 的相依宣告僅涵蓋 Flask/CORS；許多已整合 SDK 為隱式匯入，應補齊到更完整的 lockfile。
- 整合端點與網路拓樸部分硬編碼於 `config_manager.py` 與 `img_tools.py`；可透過 `bot_config.json` 支援主機特定覆寫。

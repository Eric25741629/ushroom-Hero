# AGENTS.md

## GSD 流程規範

### 環境準備
1. 啟用 Conda 環境：`conda activate mushroom1`
2. 安裝相依套件：`pip install -r requirements.txt`
3. 確認 `bot_config.json` 已配置裝置與 OCR 設定

### 啟動命令
```bash
python new_main_v2.py
```

啟動後會看到：
- Push server 啟動
- Master 模式下中控板開在 `http://127.0.0.1:5002`
- 模型載入完成
- 掃描 ADB 裝置

---

## 主要入口 (Entrypoints)

| 檔案 | 功能 |
|------|------|
| `new_main_v2.py` | 主程式，循環掃描並啟動裝置 |
| `control_panel_app.py` | 中控後端伺服器 |
| `config_manager.py` | 設定管理 |
| `bot_state.py` | 裝置狀態追蹤 |
| `miner/mining_service.py` | 挖礦核心協調器 |

---

## 架構概覽

### 遊戲自動化 (ADB/Web H5)
- **ADB**: `uiautomator2` 控制實機/模擬器
- **Web H5**: Playwright 開啟遊戲頁面

### 挖礦 AI
- **分類器**: `miner/models/classifier.py` - CNN 盤面分類
- **Planner**: A* 搜尋演算法，多步規劃與成本計算
- **Executor**: 將規劃指令轉化為 ADB 點擊動作
- **死循環偵測**: 連續 3 輪盤面/動作一致即中止

### OCR 系統
- 統一管理：`img_tools.py`
- 開神燈：`Open_gold_paddle_ocr.py` (共用 fallback 機制)
- 多 Server 優先級設定於 `bot_config.json -> global -> ocr`

---

## 設定檔 (`bot_config.json`)

### `devices`
每台裝置獨立配置：
- `backend`: `adb` 或 `web_h5`
- `enable_farm`/`enable_arena`/`enable_mining`/`enable_dungeon`
- `online_check_interval`/`lamp_check_interval`
- `mining_duration_min`: 挖礦分鐘數

Web H5 特有：
- `web_url`: 遊戲 URL
- `web_profile_dir`: Playwright 使用者資料
- `web_state_file`: 登入狀態檔案
- `web_headless`: 無視窗模式

### `global`
- `mode`: `master` 或 `worker`
- `master_url`: Master 連線地址
- `worker_id`: Worker 識別碼
- `ocr.servers`: OCR Server 列表 (支援多優先級)

### `host_settings`
依 `hostname` 覆蓋全域設定，例如：
```json
"DESKTOP-B7UMOAV": {
  "mode": "worker",
  "master_url": "https://mushroom1_dashboard.infinite25741629.uk",
  "worker_id": "desktop_b7umoav",
  "allow_web_backend": false  // 不啟動 web_h5 瀏覽器
}
```

---

## Master / Worker 模式

### Master
- 啟動中控板
- 維護本機狀態
- 接收 worker 回報，下發遠端指令

### Worker
- 不啟動本地中控板
- 回報裝置狀態至 master
- 接收 master 控制指令

---

## 喚醒/休眠策略

### 對齊喚醒
- 一般設備：每小時 00~20 分喚醒
- `emulator-5558`: 1~3 小時隨機
- `7fe98fc6`: 每小時固定喚醒 (±30 秒)

### 特殊喚醒
- **車位戰鬥**: `adjust_wake_time_for_cars()` 調整喚醒時間
- **雙週副本 (5556)**: 六/日 19:57

---

---

## 手動模式

中控板「開啟網頁 (手動模式)」用於人手接管：
1. 開頁面並進入「手動操作中」
2. 暫停自動流程
3. 關閉頁面後恢復自動流程
4. Playwright 卡住時可透過「強制刷新狀態」終止

---

## 日誌

| 檔案 | 內容 |
|------|------|
| `logs/<device>.log` | 主流程、遊戲狀態切換 |
| `logs/miner_<device>.log` | 盤面、AI 決策細節 |
| `logs/error_screenshots/` | 錯誤截圖 |
| `miner/rl_logs/<device>/` | RL 事件記錄 |

格式：`時間 - 層級 - [檔案:行號] 訊息`

---

## 常見問題

### 1. 手動模式還跑自動腳本？
確認使用新版本。現在手動開網頁會優先處理，不會掉回自動流程。

### 2. Web H5 報 Playwright 錯誤？
確認執行 `new_main_v2.py` 的 Python 環境有安裝 Playwright，且不是誤以為的 Conda 環境。

### 3. OCR 連不到？
已改為共用 OCR fallback，檢查 `bot_config.json -> global -> ocr.servers` 設定。

### 4. Worker 不該開瀏覽器卻還在掃？
確認主機在 `host_settings` 有 `allow_web_backend: false`。

### 5. 異地登錄問題
- `StartupLoginConflictError`: 啟動階段中斷
- `LoginConflictError`: 執行中中斷
兩者都會強制休眠 30 分鐘。

---

## 測試命令

```bash
# 單個裝置測試
test_device.sh <ip>

# OCR 測試
test_ocr_server.py

# 挖礦 AI 調試
python miner/scripts/debug_with_image.py <screenshot.jpg>
```

---

## 重要約束

1. **SMB/NAS 執行**: `sys.dont_write_bytecode = True` 關閉 `.pyc` 寫入，避免 I/O 卡頓
2. **模型同步**: `utils/model_sync.ensure_local_model()` 確保模型在本機 SSD
3. **UTF-8 BOM**: 檔案可能含 BOM，讀取時需處理
4. **執行緒管理**: `_running_threads` 追蹤所有裝置執行緒
5. **緊急退出**: `Ctrl+C` 會呼叫 `shutdown_web_devices()`

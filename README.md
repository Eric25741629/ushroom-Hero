# 菇勇者全自動掛機

這個專案用來管理多台裝置的掛機流程，支援兩種後端：

- `adb`: 直接用 `uiautomator2` 控制實機 / 模擬器
- `web_h5`: 用 Playwright 開啟 H5 遊戲頁面

同時內建：

- 中控板 (`control_panel_app.py`)
- 多裝置狀態追蹤 (`bot_state.py`)
- OCR server fallback 與 circuit-breaker (`img_tools.py`)
- Master / Worker 模式

如果你只想先跑起來，重點看「快速啟動」和「bot_config.json」兩節就夠了。

## 主要入口

- 主程式: [new_main_v2.py](/a:/菇勇者全自動掛機/new_main_v2.py)
- 中控後端: [control_panel_app.py](/a:/菇勇者全自動掛機/control_panel_app.py)
- 設定管理: [config_manager.py](/a:/菇勇者全自動掛機/config_manager.py)
- 裝置狀態: [bot_state.py](/a:/菇勇者全自動掛機/bot_state.py)
- Web 裝置封裝: [device_wrapper.py](/a:/菇勇者全自動掛機/device_wrapper.py)

## 快速啟動

1. 啟用你的 Conda 環境
2. 安裝相依套件
3. 確認 `bot_config.json` 已填好裝置與 OCR 設定
4. 啟動主程式

範例：

```powershell
conda activate mushroom1
python new_main_v2.py
```

啟動後通常會看到：

- Push server 啟動
- 如果本機是 `master`，中控板會開在 `http://127.0.0.1:5002`
- 模型載入完成
- 開始掃描 ADB 裝置

## 設定檔

專案主要設定在 [bot_config.json](/a:/菇勇者全自動掛機/bot_config.json)。

### `devices`

每台裝置都可以獨立配置：

- `backend`: `adb` 或 `web_h5`
- `enable_farm`
- `enable_arena`
- `enable_mining`
- `enable_dungeon`
- `online_check_interval`
- `lamp_check_interval`
- `lamp_duration_sec`
- `mining_duration_min`

如果是 `web_h5`，還會用到：

- `web_url`
- `web_profile_dir`
- `web_state_file`
- `web_channel`
- `web_headless`
- `web_stop_mode`

### `global`

`global` 負責整體角色與 OCR 設定：

- `mode`: `master` 或 `worker`
- `master_url`
- `worker_id`
- `ocr.servers`
- `ocr.server_mode`

### `host_settings`

`host_settings` 會依照 `hostname` 覆蓋全域設定。

常見用途：

- 某台機器是 `worker`
- 某台機器只做 `ADB-only`
- 某台機器不要啟動 `web_h5`

例如：

```json
"DESKTOP-B7UMOAV": {
  "mode": "worker",
  "master_url": "https://mushroom1_dashboard.infinite25741629.uk",
  "worker_id": "desktop_b7umoav",
  "allow_web_backend": false
}
```

## Master / Worker

### Master

`master` 會：

- 啟動中控板
- 維護本機狀態
- 接收 worker 回報
- 下發遠端指令

### Worker

`worker` 會：

- 不啟動本地中控板
- 將裝置狀態回報給 master
- 接收 master 的控制指令

如果某台 worker 設了 `allow_web_backend=false`，那台主機只會跑 ADB 裝置，不會自動啟動任何 `web_h5` 瀏覽器。

## 手動模式

中控板的 `開啟網頁(手動模式)` 是給人手接管用的。

目前行為：

1. 開頁面
2. 進入 `手動操作中`
3. 暫停自動流程
4. 關閉頁面後恢復自動流程

如果 Playwright 狀態卡住，中控板會在 `手動操作中` 時顯示 `強制刷新狀態`，可強制結束手動模式。

## OCR

專案有兩套 OCR 使用情境：

- 一般畫面辨識：由 [img_tools.py](/a:/菇勇者全自動掛機/img_tools.py) 統一管理
- 開神燈：由 [Open_gold_paddle_ocr.py](/a:/菇勇者全自動掛機/Open_gold_paddle_ocr.py) 呼叫，但現在也已改成共用 `img_tools` 的 fallback 機制

OCR server 由 `bot_config.json -> global -> ocr` 控制，支援多台 server priority。

## Log

log 目錄在 `logs/`。

現在主程式啟動時會：

- 先把舊的 `*.log` 改名備份
- 再建立新的同名 log

主要會看到：

- `logs/<device>.log`
- `logs/miner_<device>.log`

## 常見問題

### 1. 為什麼明明是手動模式，還會跑自動腳本？

請確認你用的是新版本。現在手動開網頁請求已經會優先處理，不會同一輪再掉回自動流程。

### 2. 為什麼 `web_h5` 會報 Playwright 錯誤？

代表目前實際執行 `new_main_v2.py` 的 Python 環境缺少 Playwright，或不是你以為的那個 Conda 環境。

### 3. 為什麼神燈說 OCR 連不到？

以前神燈模組曾經硬連單一 OCR server。現在已改成共用 OCR fallback。

### 4. 為什麼某台 worker 不該開瀏覽器卻還在掃 `web_h5`？

請確認該主機在 `host_settings` 裡有：

```json
"allow_web_backend": false
```

## 其他文件

如果你想看更細的拆分文件，可以再看：

- [README_FLASK_SERVER.md](/a:/菇勇者全自動掛機/README_FLASK_SERVER.md)
- [README_NEW_ARCHITECTURE.md](/a:/菇勇者全自動掛機/README_NEW_ARCHITECTURE.md)

## Developer Docs Index

- [Event Index Dev Guide](/C:/nas同步_project/菇勇者全自動掛機/docs/EVENT_INDEX_DEV_GUIDE.md)
- [Smart Screenshot + LLM Analyzer](/C:/nas同步_project/菇勇者全自動掛機/docs/SMART_SCREENSHOT_LLM_ANALYZER.md)

## Local Skills (Repo)

- Skill: [`event-index-observatory`](/C:/nas同步_project/菇勇者全自動掛機/.codex/skills/event-index-observatory/SKILL.md)

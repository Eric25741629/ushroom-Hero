# 新增裝置「啟用開關」修復

## 問題 (root cause, 已驗證)
新增 web_h5 裝置時:
- `register_device` (`control_panel_app.py:1497`) 立刻把裝置以全功能 live 狀態寫進 `bot_config.json`,並開啟手動登入瀏覽器 (`_run_web_login_worker`,卡在 `page.pause()` 等使用者)。
- `get_web_backend_devices` (`device_scan_service.py:60`) 回傳所有 web_h5 裝置;`scan_and_start_devices` (line 195) 在 ~30 秒內就替它 spawn 一條掛機 thread (唯一 gate 是 3 小時離線檢查,新裝置必過)。
- 掛機 thread 與登入瀏覽器同時驅動 Playwright → 互搶,就是「一直嘗試開啟這個裝置」。
- config schema 沒有任何 `enabled`/`configured` 欄位可表達「還沒設定好,先別啟動」。

## 決策 (使用者選定)
手動開關:新裝置註冊後預設「停用」,儀表板提供啟用開關,登入完+設定填好後手動啟用才會被掃描啟動。

## 設計原則
- **向後相容**:`enabled` 缺鍵 = 視為已啟用 (True)。所有現有裝置不受影響,只有新註冊的會寫 `enabled: false`。
- gate 放在「決定要不要開裝置」的真正決策點,不殺已在跑的 thread (停用只擋重啟/啟動)。
- 停用且尚未啟動的裝置仍要在儀表板看得到 (否則沒有卡片可按啟用)。

## 變更 (TDD: 先寫失敗測試再實作)

### config_manager.py
- [ ] `DEFAULT_DEVICE_CONFIG` 加 `"enabled": True`
- [ ] `DeviceConfig` dataclass 加 `enabled: bool = True`
- [ ] `update_device_config` bool 強制轉型清單加 `"enabled"`
- [ ] 新增 helper `is_device_enabled(ip) -> bool`:讀 raw config,`enabled` 缺鍵回 True

### control_panel_app.py
- [ ] `register_device`:`update_device_config` 帶 `"enabled": False`
- [ ] `get_status`:每台 `info["enabled"] = cfg.get("enabled", True)`;迴圈後把 config 中 `enabled == False` 且不在 states 的裝置補成 synthetic info (task=「未啟用」) 讓卡片顯示

### runtime_services/device_scan_service.py
- [ ] `get_web_backend_devices`:排除 `enabled == False` 的 web 裝置
- [ ] `scan_and_start_devices` spawn 區塊:`if not config_manager.is_device_enabled(ip): continue` (涵蓋所有 backend 的真正啟動決策點)

### templates/dashboard.html
- [ ] 裝置卡片:`info.enabled === false` 時顯示「🚫 已停用(設定中)」標記 + 「✅ 啟用掛機」按鈕;啟用時提供「停用」入口
- [ ] `toggleDeviceEnabled(ip, enabled)`:POST `/api/config/{ip}` `{enabled}` 後 `fetchStatus()` (沿用既有 endpoint,不新增 route)
- [ ] 設定 modal 加「啟用此裝置」checkbox:load 從 `/api/config` (`config.enabled !== false`),save 併入 payload
- [ ] 註冊成功訊息提示:需到卡片按「啟用」才會開始掛機

## 測試 (先紅後綠)
- [ ] `tests/test_device_config.py`:`is_device_enabled` 缺鍵/true/false;`update_device_config` 寫入 `enabled: false` 持久化 + 轉型
- [ ] 新測試 `get_web_backend_devices` 排除停用 web 裝置 (monkeypatch config load)
- [ ] register endpoint 寫入 `enabled: false` (monkeypatch 登入 worker thread,避免真開 Playwright)
- [ ] `tests/test_dashboard_template.py`:模板含啟用開關字串

## 驗證
- [x] `python -m py_compile` 變更檔 → PY_COMPILE_OK
- [x] `python -m pytest` 新測試 + 回歸 (device_config / smoke_config_api / config_mtime_cache / dashboard_template) → 32 passed
- [x] 掃描/web-start 路徑回歸 (bootstrap_api_services / game_initialization / web_manual_headful_override) → 15 passed
- [ ] live 驗證 (見下方步驟) — 需使用者在跑著的 bot + 真實帳號確認

## Review (2026-06-06)

### 改了什麼
1. `config_manager.py`:`enabled` 進 `DEFAULT_DEVICE_CONFIG` + `DeviceConfig` (預設 True);
   `update_device_config` 對 `enabled` 做 bool 轉型;新增 `is_device_enabled(ip)` (缺鍵=True)。
2. `control_panel_app.py`:`register_device` 寫 `enabled: False`;`/api/status` 每台帶 `enabled`,
   並把「明確停用且無 runtime state」的裝置補成卡片 (task=未啟用),否則沒卡片可按啟用。
3. `runtime_services/device_scan_service.py`:`get_web_backend_devices` 濾掉停用 web 裝置;
   `scan_and_start_devices` spawn 前 `is_device_enabled` gate (涵蓋所有 backend)。
4. `templates/dashboard.html`:停用卡片顯示「🚫 已停用」+ 只給「✅ 啟用掛機」鈕;
   `toggleDeviceEnabled()` 走既有 `/api/config` POST;設定 modal 加「啟用此裝置」checkbox;
   註冊成功訊息提示需手動啟用。

### 向後相容
缺 `enabled` 鍵 = 視為已啟用。現有所有裝置 (bot_config.json 無此鍵) 行為完全不變,
只有「透過 register 新增的」才寫 `enabled: false`。回歸測試證實既有 config flow 不受影響。

### 已知限制 (刻意,符合最小衝擊)
- 停用「已在跑」的裝置不會立刻殺 thread;只擋下次重啟。新裝置場景無此問題 (還沒 thread)。
- 按「啟用」後最多等一個掃描週期 (~30s) thread 才起來,卡片會短暫消失再出現 (自癒)。
- gate 只在「自動啟動」路徑;手動登入 worker / 獨立 CLI 不受 enabled 影響 (預期)。

### Live 驗證步驟 (dual-backend)
1. 啟動 bot (`python new_main_v2.py`),開 http://127.0.0.1:5002。
2. 點「新增帳號」→ 填 device_id + url → 建立並開啟登入。登入完關瀏覽器。
3. 確認:該裝置卡片顯示「🚫 已停用」,且 **30 秒過後仍不會** 自己開瀏覽器掛機 (修復前會互搶)。
4. 點卡片「⚙」填好設定 → 按「✅ 啟用掛機」(或 modal 勾「啟用此裝置」存檔)。
5. 確認:約一個掃描週期內裝置自動起來開始掛機。
6. ADB 後端:把某台 adb 裝置在 modal 取消勾「啟用此裝置」存檔 → 確認下次它不再被重啟。

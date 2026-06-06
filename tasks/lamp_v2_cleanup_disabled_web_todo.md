# 清掉 opengold_v2 死旗標 UI + 停用裝置可開網頁 (2026-06-07)

## 背景 (已驗證)
1. 神燈一律走 V2:`game_actions/lamp_scheduler.py:27` 明寫「`use_opengold_v2` 旗標保留於
   config 僅供相容,路由已不再讀取它」。前端設定窗的 `chkOpenGoldV2` checkbox 操控的是
   死旗標。`docs/REFACTORING_OPPORTUNITIES.md` 已列為待清死碼。
2. 停用裝置 (新註冊 enabled=false) 卡片只渲染「啟用掛機」鈕 (`dashboard.html:2434-2442`)。
   就算露出一般的「開啟網頁」鈕也按不動 ── `/api/web_launch` 只寫信箱
   `_web_launch_requests[ip]`,所有消費者都在掛機 thread 迴圈裡 (`new_main_v2.py:151`、
   `web_session_service.py:148`、`wake_up_handler.py:166`…);停用裝置沒 thread → 沒人消費。
   正解:停用卡片的「開啟網頁」改打 `/api/web_login/<ip>` → `_run_web_login_worker`
   (獨立 Playwright 手動登入,跟註冊新裝置同一條路,不需掛機 thread、不受 enabled 影響)。

## 決策 (使用者選定)
- 點1 清理深度:**前端 + 全死碼清除** (含 config schema / live config / stale doc/banner)。
- 點2:停用 web_h5 卡片在「啟用掛機」旁加「🌐 開啟網頁(登入/設定)」→ `/api/web_login`;
  adb 停用裝置無網頁不加。

## 變更 (TDD: 先紅後綠)
### 點1 — 移除 use_opengold_v2 死旗標
- [ ] `templates/dashboard.html`:移除 checkbox (1347) + load (3045) + save (3137);收掉多餘 divider
- [ ] `config_manager.py:712`:從 bool 強制轉型清單移除 `use_opengold_v2`
- [ ] `bot_config.json`:移除 7 台裝置的 `use_opengold_v2` 欄位
- [ ] `Open_gold_paddle_ocr.py:1-13`:更新 stale banner (不再提 use_opengold_v2 切換 / 3-6 裝置)
- [ ] `tests/test_lamp_scheduler.py:1-12`:修 stale module docstring (移除 V1 fallback 描述)
- [ ] `docs/REFACTORING_OPPORTUNITIES.md` / `docs/INDEX.md`:標記此死碼已清

### 點2 — 停用 web_h5 裝置可開網頁登入
- [ ] `templates/dashboard.html` 停用分支 (2434):web_h5 加「開啟網頁(登入/設定)」鈕
- [ ] 新增 `openWebForSetup(ip)`:POST `/api/web_login/${ip}` (persist_settings:false)
- [ ] `tests/test_dashboard_template.py`:
      - (紅) 停用卡片有 openWebForSetup 且走 /api/web_login 而非 /api/web_launch
      - (紅) chkOpenGoldV2 / use_opengold_v2 已從模板移除

## 驗證
- [ ] focused pytest:test_dashboard_template / test_lamp_scheduler / test_device_config
- [ ] py_compile 變更的 .py
- [ ] live (使用者):新註冊裝置卡片 → 按「開啟網頁(登入/設定)」會開登入窗;登入完關窗按「啟用掛機」

## Review (2026-06-07)
TDD：先在 test_dashboard_template.py 寫 2 個失敗測試 (openWebForSetup 走 web_login / 死旗標已移除) → RED (2 failed) → 實作 → GREEN。

改動檔：
- `templates/dashboard.html`:停用分支加 web_h5「開啟網頁(登入/設定)」(→/api/web_login);
  新增 openWebForSetup();移除 chkOpenGoldV2 checkbox + load + save + 多餘 divider。
- `config_manager.py`:bool 轉型清單移除 use_opengold_v2。
- `bot_config.json`:7 台移除 use_opengold_v2 欄位 (JSON 仍有效)。
- `game_actions/lamp_scheduler.py` / `Open_gold_paddle_ocr.py` / `tests/test_lamp_scheduler.py`
  / `docs/INDEX.md` / `docs/REFACTORING_OPPORTUNITIES.md`:更新 stale 註解/banner/docstring/doc。

驗證:`pytest test_dashboard_template + test_lamp_scheduler + test_device_config` = 30 passed;
`py_compile config_manager.py lamp_scheduler.py` OK;`bot_config.json` json.load OK。

部署注意:
- `dashboard.html` 需瀏覽器 Ctrl+F5 清快取才看得到新按鈕 / checkbox 消失。
- `config_manager.py` 改的是 bool 轉型清單,屬 Python 模組需重啟 new_main_v2.py 才生效
  (但僅影響 update_device_config 寫入時是否強制轉 bool;移除一個已不存在的鍵無 runtime 影響)。
- 開神燈路由本來就無條件走 V2,無行為變化。

點2 live 驗證 (使用者):新註冊或任何 enabled=false 的 web_h5 裝置卡片 → 應出現
「🌐 開啟網頁(登入/設定)」→ 按下開登入窗 → 登入+填設定 → 關窗 → 按「✅ 啟用掛機」。
(adb 停用裝置不應出現此鈕。)

已知限制:開著登入窗時若手動按「啟用掛機」→ 掃描器可能 spawn thread 與登入窗互搶
(與既有註冊流程同一風險);按鈕提示已要求先關窗再啟用。

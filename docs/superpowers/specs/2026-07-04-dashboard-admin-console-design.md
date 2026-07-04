# Dashboard 總後台與帳號系統設計（2026-07-04）

## 目標

在 control panel dashboard 加入「總後台」（系統設定頁），讓管理員可以：

1. 設定本機主機角色（master/worker、master URL），不必手動編輯 `bot_config.json`。
2. 管理登入帳號：多組帳號、申請/審核流程、每帳號指定可見裝置。
3. 整個 dashboard 改為登入後才能使用（目前主頁/裝置控制完全沒登入）。

## 帳號模型

兩層權限：

| 角色 | 總後台 | 裝置可見性 | 其他 |
|------|--------|-----------|------|
| 管理員（`is_admin: true`） | 可進入 | 全部裝置 | 審核申請、建/刪帳號、改可見清單、設主機角色 |
| 一般帳號 | 不可進入（403/導回主頁） | 只看 `visible_devices` 清單內的裝置 | 可用被分配裝置的所有現有功能 |

帳號狀態：`active`（可登入）/ `pending`（申請中，不可登入）。

## 申請與審核流程

1. 登入頁提供「申請帳號」連結：填帳號 + 密碼送出，建立 `pending` 帳號。
   - 帳號名已存在（含 pending）→ 拒絕並提示。
   - 申請端點做基本 rate limit（同 IP 簡單計數），防灌爆。
2. 管理員登入後，導覽列顯示待審核數量紅點；總後台有待審核清單。
3. 核准：勾選可見裝置 → 狀態轉 `active`。拒絕：直接刪除該筆申請。

## 儲存：`dashboard_settings.json`

- 位置：專案根目錄，**加入 `.gitignore`**（比照 `auth_state/`，不進版控）。
- 密碼使用 `werkzeug.security.generate_password_hash` / `check_password_hash`（Flask 既有依賴，不加新套件）。
- 結構：

```json
{
  "accounts": [
    {
      "username": "infinite",
      "password_hash": "pbkdf2:...",
      "is_admin": true,
      "status": "active",
      "visible_devices": []
    }
  ],
  "host_role": {
    "mode": "master",
    "master_url": "http://127.0.0.1:5002"
  }
}
```

- `visible_devices`：一般帳號的裝置 ID 白名單；管理員忽略此欄位（一律全部）。
- `host_role`：本機覆寫，可為 null（沿用 `host_settings` 邏輯）。
- 讀寫需經 lock（多執行緒 Flask），寫入採 tmp+rename 原子替換。
- **首次啟動遷移**：檔案不存在時，用現有 `MUSHROOM_DASHBOARD_USER/PASS` 環境變數（或 legacy fallback）自動生成第一組管理員帳號，零中斷。

## 登入保護（全站）

- Flask app 掛全域 `before_request`：未登入 → 頁面請求導向 `/login`，API 回 401。
- **豁免清單**（機器對機器與公開端點）：
  - `/login`、`/apply`（申請帳號）與其靜態資源
  - worker 同步回報端點（`routes_worker` 相關）
  - push server / live-view 的機器端點、健康檢查
  - 豁免清單集中定義於 `control_panel/shared/auth.py`，一處維護。
- 現有飛寵 `_fly_pet_auth` 整併：改為讀同一套 session key，`/fly-pet/login` 導向統一登入頁；舊入口不壞。
- Session 標記：`username`、`is_admin`。

## 裝置可見性過濾

- 過濾點：API 出口層 —
  - 裝置清單/狀態 API：回傳前依 session 帳號過濾。
  - 單一裝置的狀態/控制 API：不在可見清單 → 403。
- 管理員不過濾。
- 過濾 helper 集中在 `control_panel/shared/auth.py`（如 `visible_device_filter(devices)` / `require_device_access(device_id)`），各 blueprint 呼叫，不散寫。

## 主機角色設定

- 總後台顯示：目前 hostname、生效中的 mode / master_url 及其來源（`dashboard_settings.json` 覆寫 → `host_settings` → global 預設）。
- 可編輯覆寫值；儲存後標示「**重啟 new_main_v2.py 後生效**」（本專案無 hot-reload）。
- `config_manager` 讀取優先序改為：`dashboard_settings.json` 的 `host_role` > `host_settings[hostname]` > `global` 預設。

## UI

- 新頁「系統設定」（總後台），走現有設計系統（`static/lib/tokens.css` + `components.css` + `app.js`，經 `templates/_assets_head.html`）。
- 區塊：
  1. 待審核申請（有 pending 才顯示；核准時勾選可見裝置）
  2. 帳號管理（清單、新增、刪除、改密碼、編輯可見裝置、設/撤管理員）
  3. 主機角色（mode / master URL、重啟提示）
- 導覽列：管理員可見「系統設定」入口 + 待審核紅點數字；一般帳號不顯示。
- 統一登入頁 + 申請帳號頁（沿用飛寵登入頁樣式改造）。

## 錯誤處理與邊界

- 刪除/降權最後一名管理員 → 拒絕（至少保留一名 active 管理員）。
- `dashboard_settings.json` 損毀 → 啟動時報錯並拒絕啟動 dashboard 認證（fail-closed），不 fallback 成無密碼。
- 申請帳號的 username 做長度/字元白名單驗證（3–32、`[A-Za-z0-9_]`）。
- 一般帳號的 API 請求帶不可見 device id → 403，不洩漏裝置是否存在。

## 測試

- `tests/test_dashboard_auth.py`：
  - settings 檔讀寫/遷移（env → 第一組管理員）
  - 登入/登出、pending 不可登入
  - 申請流程：建立、重名拒絕、核准、拒絕
  - 可見性過濾與 403
  - 最後管理員保護
- 認證層以 Flask test client 測，不碰真實裝置/Playwright。

## 不做（YAGNI）

- 角色權限細分（僅 admin / 一般兩層）
- Email 通知、密碼重設流程（忘記密碼由管理員改密碼）
- DB / flask-login 套件（session + JSON 檔即可）
- 機器端點的共享 token（先豁免清單，之後要更嚴再加）

# Spec: Dashboard「加入搶佔/駐守」按鈕(本服車位戰)

日期: 2026-07-08
狀態: 待使用者過目
帳號範圍: 只小寶 `7fe98fc6`(web_h5)

## 目標

在 dashboard 小寶的裝置卡上加一顆按鈕,旁邊填 `pos`(車位編號)+ 選「搶佔 / 駐守」,
按下後透過小寶**已開啟、已登入**的 live web session,送出一次本服車位戰報名封包並回報結果。

## 背景(已 live 驗證)

- 實際「加入搶佔」送的是本服變體 `car_park.server_car_join`(cmd **12861**)
  body `{pos, queue_type}`。2026-07-08 小寶實測抓到 `{pos:1, queue_type:1}`。
- `queue_type=1` = 搶佔(攻方)**已驗證**;`queue_type=2` = 駐守(守方)**推斷**,
  首次點「駐守」時看回應碼確認(不預先為了驗證再送一發)。
- 相關輪詢:`server_car_info`(12860 空 body 總覽)、`server_car_queue`(12868 `{pos}` 該車位隊列)。
- 完整協議見 memory `reference_carpark_rob_battle_protocol.md`。

## 設計決策

### 通道:CDP,不走獨立 WS
- 用 `control_panel/shared/cdp.py` 的 `_cdp_evaluate(ip, expr, await_promise=True)`
  attach 玩家 live 瀏覽器(`web_debug_port`),在頁面跑 JS。
- 理由:符合「玩家先開網頁並加入遊戲」前提;重用玩家 session,**不另開登入**(避免
  `StartupLoginConflictError`);打在玩家看得到的同一連線。
- 不用 `ws_session.get_client()`(那是 fresh login,會和玩家瀏覽器衝突)。
- 不用 `WebGameAPI`(需 bot thread 的 page,route 拿不到)。

### 送法:遊戲自帶 encoder,零新 protobuf
- JS:`netManager.send('car_park.server_car_join', {pos, queue_type})`——遊戲用自己的
  proto class 編碼(recon 已證可行)。不寫任何 `pb_uint` body builder。

### 回報結果
- JS 是一個 async snippet(`await_promise=True`):
  1. 檢查 `window.netManager?._cnet` 存在且 WS OPEN;否則 reject → 回「請先開啟網頁並進入遊戲」。
  2. 裝一次性 `reciveMsg` 攔截 cmd 12861 的回應。
  3. `netManager.send('car_park.server_car_join', {pos, queue_type})`。
  4. 等最多 3s 收到 12861 回應 → resolve `{ok:true, hex}`;逾時 → `{ok:true, sent:true, reply:null}`(已送出但沒接到回應)。
- Route 把結果包成 JSON 回前端;前端 toast。

### 後端 route
- 新增 `POST /api/carpark_rob/<ip>`(放 `control_panel/routes_control.py`,沿用 `control` blueprint)。
- `require_device_access(ip)`(check_request_auth 已對含 `ip` 的 route 自動套 + admin gate)。
- **硬限制 `ip == "7fe98fc6"`**,其他一律 `abort(403)`。
- 讀 JSON:`pos`(int,>=1)、`queue_type`(int,∈{1,2});驗證失敗回 400。
- 呼叫 `_cdp_evaluate` 帶組好的 JS expr(pos/queue_type 以 int 內插,無字串注入面)。
- 回 `{"status":"ok", "reply":..., "pos":..., "queue_type":...}` 或 `{"status":"error","msg":...}`。

### 前端
- 只在小寶(`7fe98fc6`)裝置卡顯示一個小面板:`pos` 數字輸入框 + 搶佔/駐守 `<select>` + 按鈕。
- 按下先 `confirm('確定送出 加入{搶佔|駐守} pos={n}?')`(對應遊戲內確認框,防誤點),
  再 `fetch` POST;結果用 `toast()` 顯示。
- 沿用現有 per-device 按鈕慣例(`dashboard.html` deviceControl 區 + click-lock)。

## 不做(YAGNI)

- 不做排程/倒數/sniper(使用者選「按下立即送」)。
- 不做多裝置(只小寶一台)。
- 不做隊列狀態輪詢/顯示、不做自動退出隊列、不做 pos 下拉自動列可打車位。
- 不動 bot 主迴圈 / bot_state / device thread。
- 不新增 protobuf body builder、不新增套件。

## 測試

- 後端 route 單元測試(mock `_cdp_evaluate`):
  - `ip != 7fe98fc6` → 403。
  - `pos` 缺/非正整數 → 400;`queue_type ∉ {1,2}` → 400。
  - 合法輸入 → 呼叫 `_cdp_evaluate` 一次、參數含正確 pos/queue_type、回 200 JSON。
- 不測 live CDP/遊戲(需真連線;由使用者首點時 live 驗證,尤其 queue_type=2 駐守)。

## 風險 / 待確認

- `queue_type=2=駐守` 推斷值:首次點駐守看回應碼;若錯則對調。
- 這是**真的送出遊戲指令**(會用掉當日搶佔次數、真的參戰);confirm 對話框 + 只限小寶 + admin gate 為防護。
- 無 hot-reload:route/後端改動需重啟 `new_main_v2.py`(承載 control panel 的進程)才生效。

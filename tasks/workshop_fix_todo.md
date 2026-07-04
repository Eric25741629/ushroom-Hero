# 加工坊 (workshop) WS 修復

## 問題
使用者:每個帳號的食物「都沒在正常加工」。5554 live (CDP 9230) 實測確認。

## 根因 (live + log + state 三方坐實)
小隊加工坊 (team_cfg_id 6002 / wire ws_id 2) 長期卡在 **recipe=0(空轉沒在做食物)**。
`ws_state/emulator-5554.json` **完全沒有 `workshop` 鍵** = `_run_workshop` 從沒成功過一次。
log (`_session_bot_start.20260619`) 每輪固定: `choose_food food=8001:0 → 0x0201 error_code=3` (全裝置共通)。

四個疊加 bug (全在 `ws_token/workshop.py` + `ws_token/runner.py`):

1. **count 取錯來源 (致命)** — `switch_recipe`/`rotate_team_recipes`:
   `count = read_dining_hall().get(food_id, 0)`。dining_hall 是**成品倉**數量,不是可做量。
   8001 成品=0 → `choose_food(8001, 0)` → 伺服器回 **0x0201 error_code=3 (道具不足)**。
   正確:count 應由**原料庫存**算 `producible = min(⌊stock_mat/per_unit⌋)`。

2. **parity 永遠卡 0** — `_run_workshop`:state 只在 `any_ok=True` 時寫;成功永不發生
   → `last_rotate_ts=0` → `parity=0` 每次 → order 永遠 `(8001, 8005)`,i=0 永遠選 **8001**,
   從沒試過 8005。且 12h cadence gate 永不生效 → 每次喚醒都重跑壞輪換。

3. **每輪先 cancel 破壞生產** — running 工坊也照 cancel,把正在做的清成 recipe=0,再 choose 失敗。
   對「跑到原料歸零」型工坊,中途 cancel 是錯的。

4. **成功偵測方式錯誤** — `_mutate` 等 `(18435, 0x0201)`;但 live 證實 **choose 成功的 ack 回在別的 cmd**
   (等 18435 會 timeout),狀態卻已改。原作者把這些 timeout 誤判成 "state-gated silence=失敗"。
   正確:choose 後 **re-read 18434 的 `pw_worker_info#7.f2`**(選定食物 id)判斷成敗。

## Live ground truth (5554, 2026-06-19)
- `configFood.approach`(原料): 8001=[[6017,2]] 8002=[[6017,1],[6019,2]]
  8004=[[6017,1],[6019,2],[6020,2]] 8005=[[6019,2],[6020,2],[6021,2]] 8003=無料。
- `configFood` 時間欄: 8001=60s/個、8005=420s/個。
- `choose_food food_v` = 要生產的單位數,**必須 1 ≤ v ≤ 可做量**(伺服器不 clamp;9999 直接 code3)。
- 成敗訊號 = 18434 `food_info#2[team].pw_worker_info#7.f2`(0=閒置;=food_id=正在做)。
  `worker_status#3` (601/602) **不是**生產訊號(閒置/生產都 ~602)。
- 原料庫存讀法(已驗):0x0402 inventory push 的 item entry `{item_id#1, new_count#3}`。
  實測 5554:6019=118, 6020=118, 6021=1138 → 8005 可做 = **59**。已 `choose_food(8005,59)` 設成功。

## 暫時狀態
6002 已手動恢復:正在做 8005 ×59(約 7h)。**但 bot 下次 WS 階段會用壞輪換再清掉**,需修 code 才持久。

## 修法 (待使用者確認後動手 — 動到正在跑的 bot)
重寫 `ws_token/workshop.py` mutate/輪換 + `runner._run_workshop`:
- [ ] 新增 `producible_count(materials, food_id)`:用 configFood.approach 算 `min(⌊stock/per⌋)`。
      原料庫存來源 = `mining.InventoryTracker`(登入 0x0402 9800004 全庫存快照,bot 連線已捕獲)。
      **驗證 TODO**:確認登入快照含 6017/6019/6020/6021(高信心—全庫存 dump;否則退回 cancel-capture)。
- [ ] `choose_food`:count 改用 producible;count<1 直接跳過(不送 0 觸發 code3)。
- [ ] 成敗偵測:choose 後 re-read 18434 比對 `pw_worker_info#7.f2 == food_id`,不靠 18435 ack。
- [ ] 輪換策略:只對 **idle (f7.f2==0)** 工坊指派;**正在做目標食物就不動**(不 cancel)。
      移除「每 12h 強制 cancel→choose」;改「閒置才補配方」(原料跑完自然閒置再換下一個)。
- [ ] `_run_workshop`:state/parity 推進不再綁「成功」死結;或直接以「閒置才補」取代 parity 輪換。
- [ ] 解析 `pw_worker_info#7` schema(目前 opaque bytes):f2=選定食物,需確認其餘欄(進度/剩餘量)。
- [ ] 測試:workshop producible/choose/idle-detect 的單元測試(mock 0x0402 + 18434 reply)。

## 暫時止血選項(若先不改 code)
`runner` 加 `--no-workshop` / `workshop_rotate=False`,讓 bot 別再每小時破壞工坊。

## 探測工具 (本次新增,read 為主)
`tools/probe_workshop_live.py`(18434/18441 全 dump) `tools/probe_choose_food.py`(單次 choose 實驗)
`tools/workshop_set_parsed.py`(解析庫存→算可做量→精確 choose,= 修法演算法雛形)

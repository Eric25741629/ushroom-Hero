# 航海每日任務重構 (sea_v2) — 階段 A

設計：`docs/protocol/SEA_DAILY.md`
範圍（使用者已核准 2026-05-25）：模組骨架、定位/座標導航/挑格/滑動校準、領獎流程。
動作(駐守/進攻)留待白天 10:00–24:00 窗口（階段 B）。

## 純邏輯（TDD，先寫失敗測試）— DONE

- [x] `sea_v2/navigator.py` 座標數學：`world_to_pixel`、`world_delta_to_drag_px`(=−Δ/2.0)、`is_on_screen`、`center_drag`(closed-loop, max_step clamp) — `tests/test_sea_v2_navigator.py` 8 pass
- [x] `sea_v2/tiles.py`：`parse_tiles` / `home_base`(離鏡頭最近) / `nearest` / `pick_daily_targets` — `tests/test_sea_v2_tiles.py` 9 pass
- [x] `sea_v2/map_cache.py`：load/save(atomic,utf-8)/record_account_base/record_targets/get_* — `tests/test_sea_v2_map_cache.py` 6 pass

## 需 live 探測（夜間可做）— DONE

- [x] 領獎 UI：每個任務一個 `btnGet`（完成才 active）→ `claim_rewards` 只點 active 的
- [x] live 驗證：定位回家(✓)、worldToScreen 點擊落點(base→123,792 ✓)、pan 置中閉環 off-x 712→1 單調**無過頭**(✓)
  - 垂直 pan 在此緯度受限，但目標都在垂直視野內 → 導航目標改為「進安全框可點」而非置中

## 串接 — DONE

- [x] `sea_v2/tasks.py` `run_daily(session)` 每日狀態機 + 領獎 + 寫 cache — `tests/test_sea_v2_tasks.py` 6 pass
- [x] `sea_v2/session.py` H5SeaSession 實體（定位/讀格/閉環置中/領獎/離開；駐守/進攻/修船 = 階段B stub）
- [x] `sea_v2/__init__.py` `sea(ip,d)` + `use_sea_v2` flag（預設 off，**未接 runtime**）— `tests/test_sea_v2_entry.py` 4 pass
- [x] `python -m pytest tests/test_sea_v2_*.py -q` → **33 pass**
- [x] live 端對端 smoke：`sea_v2.sea("emulator-5554", shim)` 跑完整路徑無 crash，home/挑格/置中/領獎/離開皆正確，cache 寫入

## 修船套件流程 — 已映射 + 實作（夜間）

- [x] 路徑：`btnPort`→SeasonMainView →`btnRestore`(維修站)→SeasonRestoreView →`一鍵修築`(root/bot/btnRestore)
- [x] **emit('click') 對一鍵修築無效 → 改用 pixel tap**（UI 節點 worldPosition→pixel）；session 全面改 pixel tap
- [x] 材料閘：缺「木材」跳 `ItemGetWayView` → 偵測=料不足→關閉→return False
- [x] 導航發現：關閉港口=退出賽季回主頁 → run_daily 把修船排最後（exit 即離開賽季）
- [x] live 驗證：use_repair_kit 走完整路徑、缺料正確 return False、收尾乾淨（回主頁）
- [ ] 白天有木材時驗證一鍵修築「成功」分支落地畫面（深夜耐久滿+缺料，無法驗證成功路徑）

## 階段 B（白天 10:00–24:00）— 主要完成 2026-05-25（5560 manual-hold）

根因再修正：**worldToScreen→pixel 點 tile 會 miss**（select 節點空），改用 **OCR 點地圖標籤**
（沿用 legacy 手法）才可靠。`dual-backend-task-dev` 的「OCR 是必要手段」在此坐實。

- [x] 動作選單映射：選格後選單在**地圖場景** `/SeasonMapScene/unit/select/SelectInfo/opt/btnItem1/txtName`，
      文字即動作（free→`駐守`、enemy/relic→`進攻`、own→無選單只剩 `駐守中`）
- [x] `session.garrison` 重寫為 OCR：OCR 點最靠中心的 `資源` 標籤 → 若選單有 `駐守` → 點 `駐守`(排除 `中`) → `開始航行` → 驗 `行軍中`；自家(駐守中)會跳下一顆
- [x] `session.attack` 重寫為 OCR：OCR 點 `跡`(遺跡 OCR 常掉字成「遣跡」) → 選單有 `進攻` → 點 `進攻` → `開始航行`
- [x] `session.claim_rewards` 重寫為 OCR：迴圈點 `領取`(排除 `已領取`)，以 cocos active-btnGet 數為終止
- [x] **live 驗證（5560）**：駐守完整跑通（資源Lv1→駐守→開始航行→行軍中，駐守操作任務變可領）；領獎連點實際清掉 3 項；整合 smoke 證實真 session 之 OCR+選單讀取串接正確
- [x] OCR 子字串地雷：`領取⊂已領取`、`駐守⊂駐守中`、`遺跡→遣跡`→ `_ocr_matches` 用 exact 優先 + exclude
- [x] tests：`tests/test_sea_v2_session.py` 13 pass（_ocr_matches 歧義、garrison 跳自家挑 free、attack、claim 迴圈、修船 gate）
- [ ] **未做（刻意）**：未實打 `進攻` 遺跡——出擊會損耗船耐久(=修船任務由來)，且當日進攻/遺跡已完成；`進攻→開始航行` 與已驗證的 `駐守→開始航行` 同型

### 修船真相修正（2026-05-25，5560/5556 實測 + 使用者確認）
- [x] **`一鍵修築`(港口→維修站，木材) ≠ 修船套件任務**：實打 31855→42355(+10500，無 gate)後「使用1次修船套件」仍 0/1，它只升級維修站建築
- [x] **正確修船 = 地圖底部「維修」→ SeasonRepairView →「維修」(維修點)**；`use_repair_kit` 已改走此路徑
- [x] 閘門：船需停大本營，出航跳『僅位於大本營時才能維修船隻』；live 確認回 False 且**不離開賽季**（map overlay）
- [x] **自動回港機制（使用者確認）**：**攻略遺跡(進攻)會把船耐久歸零，船隨即自動回大本營** → 無需獨立召回。流程序：attack→claim→repair；`use_repair_kit` 在大本營 gate 上**重試**(預設 6×4s=24s)等船航回；timing 未 live 量測，可能要調
- [x] **一鍵修築入流程**（使用者要求「升級造船廠也要入流程」）：新增 `upgrade_repair_station()`，run_daily **排最後**（關港口=離賽季）；缺木材→ItemGetWayView→False；path 已於 5560 驗證(+10500)
- [x] tests：`test_use_repair_kit_*`（gate 重試→False／在家→True）、`test_upgrade_repair_station_*`（木材足→True／不足→False）、`test_run_daily_station_upgrade_is_last_before_exit`
- [ ] **未 live 驗證的鏈**：完整 attack→自動回港→repair→「使用1次修船套件」打勾（5556 船滿耐久無法觸發、5560 login-conflict）；待 production 或 ship-out 裝置實跑，順便量測回港時間調 retry budget

### 接入 runtime（2026-05-25，使用者：駐守/進攻/領獎先接，修船待補）
- [x] `game_actions/daily_pipeline.py` 加 `_sea_dispatch`：H5→`sea_v2.sea`、adb→legacy `Sea.sea`，受 `use_sea_v2` flag 控管；Task 14 改 `action_fn=_sea_dispatch`
- [x] `bot_config.json` `global.sea_v2_enabled=true`（設 false 即一鍵回退 legacy）
- [x] tests：full sea_v2 47 pass；`test_daily_pipeline` 合跑 27 pass+1 skip（opencc 被 stub 時略過簡轉測試）
- [ ] **生效需重啟 bot**（new_main_v2.py 已 import 舊 daily_pipeline；sys.modules cache）— 重啟時機由使用者定
- [ ] `pick_daily_targets` 方向偏好（家在左下→右下最近 free 資源）；目前靠 garrison 候選重試跳過自家
- [ ] H5 多台實跑一輪觀察

## 階段 C（後續）
- [ ] ADB(fc65396d) 定向：定位置中 + 2.0 世界/px 校準換算滑動 + OCR 確認；自家 base 角落用小地圖/一次性設定

## Review

階段 A 完成 2026-05-25（夜間）。純新增檔案，未改動任何 runtime：
- `sea_v2/`（6 檔）+ `tests/test_sea_v2_*.py`（5 檔, 33 測試）+ `docs/protocol/SEA_DAILY.md` + `tools/probe_sea.py`
- 根因修復：以 `定位` 取得確定原點 + worldToScreen 座標導航 + 閉環置中，取代舊「固定往右盲滑」。已 live 證明無過頭。
- 待白天：駐守/進攻/修船動作選單節點（深夜無法行動，今晚無法驗證）。

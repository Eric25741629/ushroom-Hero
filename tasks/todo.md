# tasks/todo.md（2026-06-11 重整）

> 已完成項目（含完整 Review）已移至 `tasks/archive/todo_archive_2026-06-11.md`。
> ws_token 家園三件待辦另見 `tasks/ws_token_home_todo.md`。
> 重構優先序真相來源：`docs/REFACTORING_OPPORTUNITIES.md`。

---

> ⚠ 2026-06-14 注意：本檔曾被郵件 subagent 誤用 Write 覆寫，已 `git checkout` 還原到最後提交版（493 行）。
> 你「header 之後、下面 `## 🔒 另一 session` 之前」的未提交 WIP 段落（2026-06-13/14 莊園/飛寵/carpark/lamp/
> rogue/exit-21 等完成紀錄）目前缺；那些段落的完整原文仍在本次 Claude session context，可隨時「reconstruct todo」原樣補回。所述工作本身皆已在 git 近期 commit + docs/ 內，未遺失。

---

## 🌙 2026-06-14 夜間自主批次：9 大需求 + 全面重構 + dashboard 重設計（branch `feat/overnight-2026-06-14`）

使用者夜間下 9 項需求 + 「全部接入 / 直接驗證 / 全面重構+改名 / 階段 commit / dashboard 5-8 方案」。
策略：WS 優先、一裝置一領域並行（5554/9230、7fe98fc6/9226、5556/9223），subagent 管 context。
**2 階段 commit 在 branch `feat/overnight-2026-06-14`（只加本批次動到的檔，排除 auth_state 機密 + ~80 無關 WIP；未 push）。**

| # | 需求 | 狀態 | 關鍵交付 / 協議 |
|---|------|------|------|
| 1 | 重構/複用稽核 | ✅ | `docs/REFACTOR_AUDIT_2026-06-14.md`；抽 `runtime_services/wake_parity.py` + `utils/protobuf_walk.py`；`oralce_manger→oracle_manager` 改名；hot-path 列待監督 |
| 2 | 花+奶茶每日一次 | ✅ | `runner._run_couple` 每日日期閘（`ws_state.couple.gift_date`） |
| 3 | 神器附魔倉庫 | ✅讀+GUI / ⏳分解動作 | `/inventory` 頁；module53 `info 0x3501`（live 2515 顆）；過濾=聯合搜索/賣最低/分解勾選；`split 0x350A` 推導，body 待 1 次 live 觸發 |
| 4 | 守護靈倉庫+詞條過濾 | ✅ | `ws_token/spirit.py read_spirit_info`（module77 `19713`，live 372 隻）；`/inventory` 過濾 |
| 5 | 每日郵件+滿判定 | ✅ | `ws_token/mail.py`+scheduler；`list 5377{mail_id=0=all}`/`claim 5380{0}=領全部 empty-safe`；武魂/gem 無真上限→best-effort 仍領 |
| 6 | 車友商行裝飾 CP | ✅演算 / ⏳白天 | `ws_token/carpark_decoration.py` CP=邊際屬性/成本；`car_park_skin_up 12817`、`configParking_design`；catalog dump 待 10:00-22:00 |
| 7 | 傳奇大亨擲骰 | ✅ | `ws_token/tycoon.py`（act module24 `dice 0x18A9` server-auth，live 驗證）；opt-in 預設關 |
| 8 | 煩惱消 | ✅ | 真相=左右消除(非2048)、client→Playwright；`fannaoxiao_solver/driver/scheduler`（daily_pipeline 14.6，預設關）；live 124>100 |
| 9 | 遺物平均點法 | ✅ | `ws_token/relic.py`（module17 `relic_up 0x1103` server-auth，live 驗證）；balanced=升最低等已裝備；opt-in+max_steps |

### ⚠ 待辦 / 提醒
- [ ] **重啟 `new_main_v2.py` + `control_panel_app.py`** 全部生效（含 `/inventory`、`/dashboard-redesigns/`）。
- [ ] **挑 dashboard 方案**：`docs/dashboard_redesigns/index.html`（7 個）；回「用 N 號」→ 做成正式 `templates/dashboard.html`。
- [ ] **開旗標**（皆預設關）：`ws_token.relic_upgrade`/`tycoon`/`mail_claim`、`enable_fannaoxiao`。relic 消耗碎片（有 max_steps/floor）。
- [ ] **神器附魔分解/賣**：route 先回 501，需 live 觸發一次擷取 `split` body。
- [ ] **車友裝飾**：白天商店開窗跑 catalog dump + buy round-trip 欄位號（`docs/protocol/CARPARK_DECORATION_SHOP.md`）。
- [ ] **重構待監督 pass**：device_wrapper 內部 / carpark JS / Flask envelope 21x / `STARTUP_SLEEP_SEC_BY_DEVICE` 改名 / `ws_token/codec` walker / `gold_mananer` 改名。

### Review
9 項全以 WS 優先 + 一裝置一領域並行完成，協議皆 live 驗證；2 階段 commit（機密/無關 WIP 已排除，未 push）。新測試全綠（relic18/tycoon15/mail19/spirit22/inventory11/carpark13/fannaoxiao8+21/scheduling10/protobuf24 + runner132）。`docs/protocol/` 新增 5 份 recon；memory 補 8 條。

---

## 🔒 另一 session 進行中（本 session 勿動）

- **挖礦 WS**：由另一個 Claude session 處理中（2026-06-11）。涉及 `ws_token/mining*.py`
  （mining.py / mining_adapter.py / mining_h5_executor.py / mining_supervised.py）與對應測試。
  本 session 不要編輯這些檔案、不要重複規劃挖礦 WS 相關工作。

---

## ⚠ 待重啟生效（改完未重啟 = 白改）

- master（infinite）+ worker（desktop_ov0asq4）的 `new_main_v2.py` 需重啟，套用：
  1. 掉線 1h 判離線 fix（2026-06-11）
  2. 手機離線 WS 降級 + 純 adb 被動撈 token（2026-06-11）
  3. control_panel_app blueprint 拆分（2026-06-11；重啟後做 dashboard live smoke = P3-CP-8）
  4. S0-wire：online_guard 純 WS online-check 後端（2026-06-11；改 web_session_service，預設關，
     需在 checker device config 設 `online_check_via_ws: true` 才啟用）
- 重啟後待驗：passive token scrape 實機 live 驗證（等手機/模擬器下次冷啟）；
  若有開 `online_check_via_ws` 的 checker，順帶驗 WS 互檢回 busy/offline/未定三態正確。

---

## 進行中 / 待辦

### 手機fc 純 WS 停車：日/夜雙窗口 + 跨界 + 泊銀9/10（2026-06-13，本 session）

Plan：`C:\Users\Eric\.claude\plans\gentle-cuddling-eagle.md`（使用者已核准，後續兩次語意修正）。
決策（最終，2026-06-13 recon 後）：整合進 ws_token runner（不另建排程）；不做在線保護；窗口內持續補停。
**範圍修正**：泊銀=跨界停車的檔次（pool 3，鉑銀1..30 = ceng 5..34，search type=4 一次回全部 68 lot，
停車同一支已驗證 12847）。配額=日 1 跨界 / 夜 0；跨界限定泊銀、優先鉑銀9/10（ceng 13/14）、滿了退
其他泊銀 lot。「5 本服/好友」遊戲內建自動化，**不做**（使用者 2026-06-13 指示）。

- [x] 1. `ws_token/carpark_plan.py` 窗口/配額純邏輯 + `tests/test_carpark_plan.py`
- [x] 2. `carpark.py` `auto_select_and_park_many`（泊銀限定、鉑銀9/10 優先、多坐騎跨 lot 補停）+ `tests/test_carpark_many.py`
- [x] 3. `runner.py` `_run_carpark` plan 路徑 + run_device 參數 + `tests/test_carpark_runner_plan.py`
- [x] 4. `ws_phase.py` 接線 carpark_plan + 補漏 carpark_auto + 測試
- [x] 5. `config_manager.py` DEFAULT + `_merge_carpark_plan` sanitizer（silver_levels 1..30 驗證）
- [x] 6. `ws_runner_service.py` 改讀巢狀 ws_token.carpark_plan（舊平面 key 相容保留）
- [x] 7. Stage 0 recon（純 WS 唯讀，小寶）：泊銀=跨界 pool 3；search type=4 回全部 68 lot
      （ceng 1..68，泊銀=5..34=鉑銀1..30）；type=2=本服(master 1467)、type=1=好友；type=3/5 無回應
- [x] 8. silver 實作（同跨界協議 + ceng 過濾/排序，無需新 cmd）
- [x] 9. carpark_smoke `--plan` dry-run；live 驗證：小寶選位 dry（30 可停泊銀、首選鉑銀9 pos=1 ✅）
- [x] 10. bot_config.json 手機fc 開 carpark_plan（day cross=1 / night 0 / 鉑銀[9,10]）

- [x] 11. 追加需求（使用者 2026-06-13 二次指示）：窗口改台灣 10:00-22:00（跨界僅此時段，
      一人限 1 台）；抱團優先（null_num 升冪 = 越滿越前）；carpark 移到 runner 第一個任務
      （搶位）；純 WS 收益領取 12846（清單 12845；空倉回 0x0201 code=173，LIVE 小寶驗證），
      plan 路徑每輪喚醒先領再停

- [x] 12. 抱團=同服（使用者三次指示）：login s2c #3=server_id（LIVE 1467）；排序加同服占用數
      降冪（count_same_server 掃 info_list/ext kv；無匹配安全退回占用數）；預讀候選上限 8
- [ ] 13. 開窗（10:00-22:00）後跑 `python tools/carpark_cluster_probe.py` 採樣占用者 attrs，
      確認同服欄位 id；若 kv 值不含 server_id 要修 count_same_server

#### Review（2026-06-13）
- 108 tests 綠（plan/many/runner-plan/config/ws_phase/wiring + 既有 carpark 44）。
- 文件：`docs/protocol/CARPARK_AUTOMATION.md` 新增「純 WS 日/夜窗口跨界停車 plan」節。
- ⚠ **需重啟 master bot（new_main_v2.py）才生效**（runner/ws_phase/config 都動了）。
- 真實停車驗證待手機fc 下次 day 窗（08:00-20:00）喚醒：看 main.log `ws_token carpark: parked` +
  `ws_state/adb-fc65396d-*.json` 的 `carpark_plan.day`。本服/好友 5 台不做（遊戲內建）。

### 手機fc 離線純 WS 掛機備援 offline_fallback（2026-06-12，本 session）

Spec：`docs/superpowers/specs/2026-06-12-ws-offline-fallback-design.md`（使用者已核可方案 B 混合備援）。
缺口只有三塊（中途斷線降級 2026-06-11 已做）：離線時 thread 不 spawn、init 連線失敗直接死、無 dashboard 開關。

- [x] 1. config：`ws_token.offline_fallback`（預設 false）進 `config_manager.py` DEFAULT；
      `bot_config.json` 手機fc 設 true（`_merge_ws_token_phase_config` 補 bool 強制轉型）
- [x] 2. `device_scan_service.py`：`get_ws_fallback_devices()`（backend=adb + ws_token.enabled
      + offline_fallback + enabled）→ 掃描注入（鏡像 ws_runner 注入；`tests/test_device_scan_ws_fallback.py`）
- [x] 3. `new_main_v2.py`：init 連線失敗 + 開關開 → WS 等待迴圈（helper 抽到
      `runtime_services/ws_fallback_service.py`：`should_ws_fallback` + `run_ws_fallback_wait_round`；
      每輪 `_run_ws_phase_for_wake` + `run_sleep_cycle(phone_offline_ws_only)` + `continue` 重試 init；
      update_state「WS 備援掛機中，等待手機回線」；例外只記 log 不炸 thread；
      `tests/test_wake_ws_fallback.py`）
- [x] 4. dashboard：`chkWsOfflineFallback` checkbox（方案 adb+ws 時顯示）+ saveConfig merge
      進 ws_token（`test_dashboard_template.py` 追加）
- [x] 5.（後端部分）focused tests 全綠：`test_device_scan_ws_fallback` / `test_wake_ws_fallback` /
      `test_wake_phone_reconnect` / `test_wake_loop_escape` / `test_ws_phase`（52 passed）；
      dashboard template 由 §4 agent 驗
- [x] 5b. 審查修正：(a) host gate `ws_token.fallback_host`（不分大小寫；空 = 只有 resolved
      mode=master 注入）防 NAS 同步 master/worker 雙注入互踢 — `ws_fallback_host_allowed`
      進 `get_ws_fallback_devices` + `should_ws_fallback` 雙保險；fc 設 `"fallback_host": "infinite"`；
      (b) `run_ws_fallback_wait_round` 的 run_sleep_cycle 失敗 except 加 60s floor sleep 防
      hot-spin；run_sleep_cycle 改 lazy seam `_load_run_sleep_cycle`（保持 scan 端 import 輕量）
- [x] 5c. 主審整合驗證（2026-06-13）：全套 88 tests 一起跑綠（scan/wake/ws_phase/ws_runner_wiring/
      scan_absence/dashboard_template）；py_compile OK；本機（master infinite）live sanity：
      fc host_allowed=True、should_ws_fallback=True、injected=[fc]、5554 不誤入。
      順修 pre-existing 測試污染：`test_wake_phone_reconnect.py` 的 device stub 缺
      `get_adb_devices` → 與 `test_device_scan_absence.py` 同跑炸 collection（HEAD 重現，非本案造成），
      stub 補一行修掉
- [ ] 6. 提醒：master+worker `new_main_v2.py` 與中控需重啟生效；手機離線時段 live 驗一輪
      （log 看到 WS 階段跑完 + 對齊睡眠 + 手機回線恢復）

### Review（手機fc offline_fallback，2026-06-13 主審結論）

- 實作 = spec 三缺口全補：掃描注入（host-gated）、init 失敗 WS 等待迴圈、dashboard 開關；
  中途斷線路徑沿用 2026-06-11 既有 `PhoneUnreachableError` 降級，未動。
- 審查抓到並修掉 1 個 critical（NAS 同步雙主機注入互踢 → `fallback_host` host gate）
  + 1 個 minor（sleep 失敗 hot-spin → 60s floor）。
- 已知可接受行為：手機離線 >1h dashboard status 仍會被 absence rule 標 OFFLINE（2026-06-11
  使用者要求的卡片轉離線），但 step 文字會顯示「WS 備援掛機中」；備援輪 WS 登入會踢
  帶出門正在玩的人（spec §3.5 已記載，緩解 = 既有 kick 偵測，要全保護需補 online_check_target_pid）。

> 注意：下方泊銀停車計畫的「手機離線時 wake loop 不會跑」假設在本案落地後失效，屆時可改掛備援輪。

### adb-fc65396d 每日 10:00 泊銀9/10 自動跨界停車（2026-06-12，本 session）

背景：該手機目前**無**自動跨界停車（carpark_scheduler 限 web_h5；`ws_token.carpark_target` 為 null）。
手機不在 ADB 上時裝置 wake loop 不會跑，所以不能掛 ws_phase，要做 master 端每日排程，
用既有 `auth_state/_auth_capture_adb-fc65396d-...json` token 直連 WS。
需求（使用者 2026-06-12 確認）：每天台灣 10:00 後、手機**不在 ADB**（在線就跳過，避免 WS 登入踢掉手機 session）
時，停 1 台到泊銀9，滿了改泊銀10。只停不收。

- [ ] 1. `ws_token/carpark.py`：`car_park_info` 解析補 `master_name`；新增
      `park_into_named_lots(client, names)` — search type=4 → 逐 lot read_lot 比對名稱
      （正規化簡繁「泊银9/泊銀9」）→ 停第一個空 pos；名單內全滿回 no_free_slot（測試先行）
- [ ] 2. 新增 `runtime_services/carpark_daily_service.py`：背景 thread 每 60s 檢查
      （台灣時間 ≥ 設定 hour、今天未成功、目標裝置不在 `adb devices`）→ 載 creds → WS 登入
      → park_into_named_lots → 記錄當日結果（json record；連線失敗當日內重試，停滿/成功即記完成）
- [ ] 3. config：device `ws_token.carpark_daily = {"enabled": true, "lot_names": ["泊银9","泊银10"], "hour": 10}`；
      `config_manager.py` DEFAULT 加預設（disabled）
- [ ] 4. `new_main_v2.py` 啟動接線（lazy runtime service，比照其他 service）
- [ ] 5. 測試：名稱比對 / 排程 gate（時間、ADB 在線跳過、一天一次）/ runner 不受影響
- [ ] 6. Live 驗證：手機離線時段跑一次 dry（search+名稱列舉），確認泊銀9/10 可由 master_name 定位，再真停
- [ ] 7. 提醒使用者：master `new_main_v2.py` 需重啟生效

### 挖礦 planner 優化 + default 重選（2026-06-12，本 session）

背景（2026-06-12 benchmark，校正後 sim seed200×30 + 299 真實 board）：
v1 score 948/cost 186 最佳；v4 最快(mean 1.9ms)但 empty-plan 7.02%（21/299 投降，v1 只 2%）、
fallback 2.7%；v4 常數(BOMB_COST=3.5 等)是舊 9× 過密 sim 掃的；所有 planner 都不會對
「貼底緣、可能延伸到視窗外的 cluster」延遲用炸彈（真實 3x3 從不完整出現在單一 frame）。

2026-06-12 使用者改方向：**不修舊 planner（T1 修復 agent 已停）**，改寫全新 v5 機率型演算法一起比。
核心想法（使用者）：空格分布非均勻 → 用歷史經驗學「下方也是空格」的條件機率，挖期望成本最短的
往下路徑；機率要可驗算；挖礦深度（高度）runtime 抓得到，priors 可依深度條件化。

- [x] T1 歷史統計驗證（2026-06-12 完成）：343 session / 350 條重建 tape / 65598 格。
      非均勻成立：P(air|air)=27.9%（1.5x lift）、P(pit|pit)=42.5%（13.8x）、貼底 pit 寬 w=1/2/3
      下延機率 2.1%/43%/77%；深度相關顯著但效應極小 → pooled priors 即可。
      產出 miner/v5/priors.json + docs/MINING_V5_PRIORS.md + tools/build_v5_priors.py（可重跑）
- [x] T2 實作 miner/v5/（2026-06-12 完成）：v4 骨架 + priors（期望成本下行、pit 續挖 bias、
      貼底殘缺正方炸彈延遲、row0 救援、empty-plan 護欄、250ms deadline）；13 tests green
- [x] T3 接入 benchmark（mining_sim_eval / compare_planners / replay_real_boards 都加 v5）
- [x] T4 四套對比（2026-06-12）：真實 board 312 張 v5 empty% 0.96%（四套最低，v4 6.73%/
      v1 1.92%）、ms_max 26ms、0 違規；sim v1 948 仍最高、v5 915 持平 v3/v4
- [x] T5 default 切 v5：config_manager 三處 + mining_service dispatch（plan_v5 + depth 參數）
      + fallback + dashboard 選單（移 v2 加 v5）+ routes_status fallback；CLAUDE.md /
      planner-eval SKILL.md 已更新；被停掉的舊「修 v4」agent 殘留改動已 git checkout 還原。
      相關測試 79 green。⚠ 需重啟 bot + 中控生效
- [ ] T8 live 驗證 v5 + 深度追蹤：emulator-5556（使用者指定），manual-hold 取得獨佔，
      看 miner.log 的 depth=N (+k) 與 planner=v5 stats；確認無 empty-plan 連發/卡死
- [ ] （既有問題，非本次造成）tests/test_mining_service_shovel_tracking.py import 時 stub
      miner.core.vision_utils 進 sys.modules 不還原 → 排在 test_mining_screen_check.py 前會
      害後者 2 個測試假失敗（順序相依污染）。修法：改用 fixture + monkeypatch.setitem 或測完還原

2026-06-12 使用者追加兩需求：
- [x] T6 深度/捲動追蹤（2026-06-12 完成）：新 miner/depth_tracker.py（DepthTracker.update/
      set_absolute_depth/last_uncertain；best_scroll 對齊核心抽共用，track_pits_replay 改委派）；
      mining_service 接線純加（depth log + plan stats depth=N）；12 tests green。
      ⚠ 需重啟 bot 生效。原設計：
      a) **權威來源 = WS**：block_id = depth*100+col、MineBoard.baseline（=row5 深度錨點），
         viewport_top_depth = baseline - 5（ws_token/mining_adapter.py:71，adapter 輸出已含
         top_depth）→ WS 路徑直接拿絕對深度，免 OCR 免猜
      b) 純截圖路徑（mining_service 視覺迴圈）fallback：連續兩張 board row-shift 比對累計相對深度
      c) 深度傳入 plan_v5(depth=...) + 寫進 miner.log/plan stats；⚠ ws_token/mining*.py 是另一
         session 的範圍，只讀不改，接線做在 v5/mining_service 這側
      d) live 驗證裝置：使用者指定 **emulator-5556**（manual-hold 取得獨佔再動）
- [x] T7 動態 priors（2026-06-12 完成）：miner/v5/priors_runtime.py（PriorsAccumulator 捲動揭露
      列觀測 + 原子寫 miner/v5/runtime/priors_runtime_<device>.json，已 gitignore）；合併規則 =
      線上計數封頂離線 n 的 20% 再合併（防單裝置偏差淹沒 65k 離線基底）；plan_v5(device=) 走
      merged priors（mtime 快取）；build_v5_priors.py --include-runtime 可覆核漂移；
      28+14 tests green、replay v5 無退步（empty 0.96% / ms_max 23.7）。⚠ 需重啟 bot 生效

### 菇菇雕像每週消耗（statue_weekly）查驗紀錄 + 待辦（2026-06-12）

查驗結論（log 驗證，無排程 bug）：
- 消耗果蔬的就是 `game_actions/statue_weekly.py`（菇菇雕像每週五一鍵消耗），僅週五觸發、成功才記錄、失敗下次喚醒重試。
- 2026-06-12（週五）手機 fc65396d 跑 3 次 = 失敗重試：01:55 / 03:29 都死在 `send_keys(clear=True)` 的 ADB_KEYBOARD_CLEAR_TEXT 廣播 null reference（未輸入、未消耗）；04:56 成功並記錄，本週不再跑。非週五日子 log 全是「排程跳過」。
- 手機畫面看到輸入 1 = config 測試值沒改回來。

待辦：
- [ ] `bot_config.json` 兩台（emulator-5554、adb-fc65396d）`statue_weekly.amount` 1 → 7000（原註解：驗證穩定後改 7000）
- [ ] 修 ADB flow `send_keys` clear 不穩：輸入框預設為 0，改不帶 clear 直接輸入或改走 `adb shell input text`（`statue_weekly.py:425` 附近）

### 地獄之門 純 WS 串接（小寶 7fe98fc6，2026-06-12 研究中）

協議已解出（client JS + `docs/protocol/MAIN_CHAPTER_PROTO_SCHEMA.json`，2026-06-12 live login 驗證 token 有效）：
- **地獄之門 = `main_chapter` 模組(13)**，不是 dungeon.py 的深淵(type2)/萬神(type23)。
  - `main_chapter_info` 3329 c2s{} → s2c{part_id#1, is_unlimited#2}：今日可打關卡。
  - `main_chapter_enter` 3330 c2s{part_id#1} → s2c{part_id, is_unlimited, battle_checkout#3, random_seed#4:uint64, roles#5:p_battle_role[]}：進場拿戰鬥種子。
  - `main_chapter_kill_reward` 3332 c2s{part_id, unit_id, pos} → s2c{..., reward_list#4}：**戰鬥中擊殺掉落**（= ADB「恭喜獲得/游荡哥布林」）。
  - `main_chapter_result` 3331 c2s{part_id#1, result#2, manual_operators#3, operators#4:p_battle_operator[]} → s2c{code#1, part_id#2, is_unlimited#3}：結算。
  - `main_chapter_reward_info` 3333 / `main_chapter_claim_reward` 3334：領獎。
- ADB `battle/special.py::hell_door`：進場→挑戰→client 端實時掛機 **10 分鐘**→討伐結束→恭喜獲得。**無掃蕩、單次**。
- 整合設計文件 `2026-06-10-...integration-design.md:100` 原本把地獄之門列在 WS **不跳**清單。
- ⚠ 時間窗：地獄之門只能在**每小時 :00-:20** 進場（使用者告知）；被 boss 打死會立刻跳出。

使用者決策（2026-06-12）：**不自己造封包偷跑**。先用 ADB/H5 真打一場，被動抓包讓 server 自己吐
真實 `main_chapter_result` 的 operators，判斷純 WS 可行性。

待辦：
- [ ] 下一個 :00-:20 窗口：小寶 7fe98fc6 開 H5(CDP 9230) → `python tools/watch_ws.py --port 9230
      --seconds 800 --out logs/hellgate_capture.jsonl` → 真打一場地獄之門(進場→等10分→領獎)。
- [ ] 解碼 capture 的 3331 send frame：operators#4 是否為真實大量回放序列。
      → 若 operators 非空且 server 用 random_seed 回放驗算 = 純 WS 不可行，維持 ADB/H5。
      → 若 operators 空/極簡仍 code=0 = 純 WS 可行（只需 enter→sleep~10分→result）。
- [ ] （若可行）`ws_token/hellgate.py`：build/parse/orchestrator + `tests/test_ws_token_hellgate.py`
      （照 `tests/test_ws_token_dungeon.py` + `tests/fakes/ws_fakes.py`）。注意 sleep 10 分鐘的設計
      （WS 階段不可 block 其他任務；可能要獨立排程或放最後）。
- [ ] 接線：`runner.py` TASK_ORDER + `_run_hellgate`；config 加開關；`ws_phase.WS_TO_PIPELINE_SKIPS`
      加地獄之門條件式 skip；`daily_pipeline.py` Task 1（`:187`）包 `if not _ws_skip("地獄之門")`。
- 探測腳本 `tools/tmp_hellgate_probe.py` 已寫但**作廢**（偷跑即時結算方向錯，使用者否決）。

### WS 階段 token bootstrap（adb 裝置首輪自動取 token，待使用者過目）

問題：adb+ws 裝置（如手機 fc65396d）從未撈過 creds 時，每輪 WS 階段
`FileNotFoundError` → 全跑 Playwright；被動撈取又只在 App 冷啟動時有料，
warm resume 永遠撈不到 → 永遠 bootstrap 不起來。

方案：WS 階段開跑前，若 creds 缺失（或 WS 登入失敗一次），且 backend=adb，
主動 `refresh_creds(ip)`（= 冷重啟 App ~30s → logcat 撈 → 寫 capture）→
`am force-stop` + HOME 鍵回桌面 → 再跑 WS 階段。WS 登入本來就會踢 App
session，先殺 App 無副作用。

- [x] 1. TDD：tests/test_ws_phase.py 加 bootstrap 案例（creds 缺→refresh 被呼叫→
      force-stop→run_device 照跑；refresh 失敗→降級回 frozenset()；
      web_h5 backend 不觸發）
- [x] 2. ws_token/ 新增 bootstrap helper（creds 存在性檢查 + refresh_creds +
      adb force-stop/HOME，best-effort 絕不拋）
- [x] 3. game_actions/ws_phase.py：run_ws_phase 接 bootstrap（cfg 開關
      `ws_token.bootstrap_token`，預設 true、僅 adb backend 生效）；
      WS 登入失敗（ticket 過期）也重撈一次再試（adb 版自癒，對齊 web_h5
      的 ws_ticket_refresh）
- [ ] 4. live 驗證：手機 fc65396d 下一輪喚醒應看到 refresh→WS 階段成功→skip-set
- [ ] 5. 重啟跑 bot 的那份 checkout（C:\python_project）

⚠ 注意：fc65396d user0 與 emulator-5554 同帳號，WS 登入會互踢 session，
兩台同時開 ws 方案需錯開或確認可接受。

### 跨界停車純 WS 自動選位（本 session，pilot 小寶 7fe98fc6）

目標：消除 `carpark_target` 手動值 — 用 `cross_car_park_preview`(12830) 自動取得跨服 lot 清單，
自選有空位的 lot 停 1 台（仍只停不收，type==3 限定）。

- [x] 1. TDD：tests/test_ws_token_carpark.py 加 search(12808)/null_space 解析 + auto_select 測試（37 綠）
- [x] 2. ws_token/carpark.py：parse_null_spaces / read_cross_null_spaces / first_free_cross_pos / auto_select_and_park（search-based）
- [x] 3. ws_token/carpark_smoke.py：--search / --preview / --info / --auto-park
- [x] 4. live recon（小寶）：CDP 釘 cmd + 客戶端原始碼確認 NEW 流程用 search type=4（非 preview）；pos 1-based、容量10、space_list 只列占用
- [x] 5. live 驗證 auto-park 一台成功（cmd 0x322f SUCCESS，mount_id=1 停入 1001001065 pos=1，可遊戲手動收回）
- [x] 6. runner 接線：carpark_auto 旗標（--carpark-auto / ws_token_carpark_auto）+ docs/protocol/CARPARK_AUTOMATION.md 註記

**完成 2026-06-11**：純 WS 跨界停車全閉環。56 測試綠（carpark 37 + runner wiring/phone 19）。
runner 預設未開（carpark_auto=False）；要啟用設 device config `ws_token_carpark_auto: true`。只停不收。

### WS 後端整合（branch `feat/ws-token-integration` / `feat/dragon-realm`）

**已 live 跑的 runner 任務**：main_tasks、league_solo、guild、steward、redpack、lamp（gate `ws_token_open_lamp`）。

**✅ 已全部 build + 整合（2026-06-11 盤點更正：先前「待 build」表已過時）**：

下列 6 feature 模組皆已實作、有 smoke、且已接進 `ws_token/runner.py` 的 `TASK_ORDER`
＋ `runtime_services/ws_runner_service.py` 的 config 映射（device config key → run_device 參數）：

| feature | runner 任務 | config key（device 層） | 風險/備註 |
|---|---|---|---|
| 轉盤金幣 | turntable | （無；免費次數無條件跑） | 只轉免費次數；WS 拿不到看廣告加倍 |
| 掛機/離線獎勵 | idle_reward | （無；online+offline push 無條件領） | 離線 type2=login push；8h 上限 |
| 跨界停車 | carpark | `ws_token_carpark_target` / `ws_token_carpark_auto` | 只停跨界 type==3；預設關 |
| 深淵之門 | dungeon | `ws_token_dungeon_sweeps`=[(type,id,num)] | **掃蕩 only，永不自動打**（anti-cheat） |
| 週副本(萬神) | dungeon（同上） | 同 dungeon_sweeps（type=23, gtid 1081） | 同上，掃蕩 only |
| 農場/打工 | farm | `ws_token_farm_config`={seed_id?,team_cfg_id?} | 空 seed=用免費種子；harvest 無條件跑 |

→ 程式整合面 **零殘項**。要啟用某裝置某功能只需在其 device config 填上對應 key。

**仍待 live 釘的 config 值（非程式工作，要實機跑一次才知道）**：
深淵/萬神 dungeon type+id+num、農場 seed_id/team_cfg_id、豐收卡 shop_type/id。
（未填 = 該功能自動 skip，不會誤動。）

**共通風險**：副本 anti-cheat（只走掃蕩、勿送 client result=0）；看廣告加倍 WS 拿不到。

**kick/異地登入（建構中）**：被踢訊號 = cmd 259（0x103, body `{1:20}`）+ 斷線 → 30 分冷卻 → online-check 再查。

**整合步驟殘項（2026-06-11 重新盤點）**：
- [x] S6b 神燈接進 runner — **已完成**（`open_lamp` 旗標 + `_run_lamp`，gate `ws_token_open_lamp`）
- [x] guild_members_info(cmd 7440) — **已完成**，落在 `ws_token/online_guard.py`
      （`build_members_body`/`parse_members`/`is_role_online_in_guild`，非 guild.py，功能等價）
- [x] S0 `ws_token/online_guard.py` 純 WS 在線檢查模組 — **已完成**（is_role_online 好友列表 +
      is_role_online_in_guild 公會成員兩路；76 測綠）
- [x] **S0-wire（2026-06-11 完成，TDD）**：online_guard 接成純 WS online-check 後端。
      新增 `online_guard.friend_presence`（tri-state Optional[bool]，不在名單=None 不再誤判 offline）、
      新模組 `runtime_services/ws_online_checker.check_via_ws`（一次性 login ticket 查好友→公會 fallback，
      任何例外/未定=None 絕不放行，client 必 close）、`web_session_service.process_online_check_requests`
      加 `_checker_uses_ws` 旁路（device config `online_check_via_ws` 預設 false→走舊瀏覽器路徑）。
      config 新 key：`online_check_via_ws`(checker 端開關)、`online_check_guild_id`(checker 端公會 fallback)。
      33 新/相關測試綠 + 87 回歸綠；py_compile 乾淨。⚠ 動到 live 互檢路徑（web_session_service 被
      new_main_v2 引用）→ **待重啟 master+worker 才生效**；S7 5556 pilot 可順帶 live 驗 WS checker。
- [ ] S7 5556 pilot live 驗證（撈 ticket → 跑一輪；非程式，等停機窗）

### S0-wire：online_guard 接成純 WS 在線檢查後端（2026-06-11 規劃，待使用者過目後執行）

> 動機：手機帳號流程要保護真人玩家在線時不踢人。現行 checker 判定走 `_run_checker_protocol_only`，
> 依賴 checker 端開著 web_h5（Playwright）session。若 checker 本身就是純 WS 裝置（無瀏覽器），
> 現行路徑無法服務 → online_guard 用自己的 login ticket 直接查好友/公會線上狀態補上這個洞。

**對外不變**：mailbox 介面（submit/pop/complete/fail/wait）零改；`online_check_checkers` 設定語意不變。

**設計（最小侵入，新增不改舊）**
1. 新 `runtime_services/ws_online_checker.py`：`check_via_ws(checker_ip, target_pid, logger) -> Optional[bool]`
   - `load_creds(checker_ip)` → `WSGameClient` connect（短 timeout）→ `online_guard.is_role_online(target_pid)`；
     好友列表查不到 → fallback `is_role_online_in_guild`（需 checker 自己的 guild_id，從 login/設定取）→ 仍 None 回 None。
   - 一律 `finally: client.close()`；任何例外回 None（讓 requester 換手或重試，**絕不誤放行**）。
2. `web_session_service.process_online_check_requests`：在 `_run_checker_protocol_only` 前加分支 —
   若 checker 的 device config `online_check_via_ws: true`（預設 false）→ 走 `check_via_ws`，否則維持原瀏覽器路徑。
   None 結果沿用既有 `fail_online_check_request`（換手/重試）邏輯，行為一致。
3. config：新 device key `online_check_via_ws`（預設 false）；不填 = 行為 byte-for-byte 不變。

**TDD**：先寫 `tests/test_ws_online_checker.py` —
   - is_role_online True/False/None 三路；好友 miss → guild fallback；client 例外 → None；close 必呼叫（fake client）。
   - process_online_check_requests：`online_check_via_ws=true` 時呼 check_via_ws 不碰瀏覽器路徑；false 時走舊路徑。

**風險/守則**
- 動到 live 互檢路徑（`web_session_service` 被 `new_main_v2` 引用）→ 落地後需重啟 master+worker 才生效。
- checker 用自己的 ticket 查好友＝需 checker 帳號與被保護玩家為好友/同公會；查無 → None（保守 skip，不放行）。
- 不改 mailbox、不改 `_run_checker_protocol_only`，只加旁路分支；舊路徑回歸測試必須續綠。

### 降運算 Phase 2（需 live 驗證，等拍板）

- [ ] 2.1 Chrome `--disable-gpu` / headless（本機最大 GPU 削減；需驗 Cocos 畫面+OCR）
- [ ] 2.2 Cocos `cc.game.setFrameRate` 限 FPS（該 build 是否支援未驗）
- [ ] 2.4 `cv2.matchTemplate` 前 2× 縮圖（需重調 0.8 門檻）
- [ ] 殘留重疊：`wake_up_handler.py:322` 舊分流延遲與 F4 啟動錯峰疊加（動時須保 5554 online-check 提前 return）

### 重構待辦（真相來源 docs/REFACTORING_OPPORTUNITIES.md，依其 Phase 順序；細節/陷阱以該文件為準）

**前置（最先做）**
- [ ] 收尾未 commit 工作區項：`oracle()` 優化、`get_stage` OCR 合併、bot_state Phase 2（皆已實作+測試，待 commit）
      ⚠ 工作區目前混有挖礦 WS session 的改動，等該 session 收尾後再 commit，避免 diff 互混

**Phase 0 — 死碼刪除 / cruft（✅ 2026-06-11 完成，commit `25542e41`；審查 APPROVED）**
- [x] `git rm --cached` 4 個 `tests/__pycache__/*.pyc.NNNNN`
- [x] `git rm main.py` + 移除 `SCRIPT_ARCHITECTURE.md` 條目
- [x] 刪 `game_state/detector.py` `new_stage_check()`（grep 確認零 .py 呼叫者）
- [x] scratch 腳本歸位（fix_prints/quick_test 刪除；test.py/dashboard_test→tools/scratch/；
      benchmark_screenshot→tools/；lian_shan_example→docs/examples/；5 個 root test_*.py 去前綴→tools/debug/；
      spin_and_send_gold_single_runner 依規未動）
- [x] root `pyproject.toml` testpaths=["tests"]；`test_item_placement_guards.py` 已一併移入 tests/
      並修 stale import（miner.planning.executor / miner.scripts）+ 斷言對齊現行 base_label 契約
      （註：`test_json_manager.py` 為 print-based scratch，doc 未指派去向，留 root，TESTING.md 已記載）
- [x] 刪孤兒 worktree 目錄 ×5（~37.7k 檔；註冊 worktree miner-reverse-search/bugfix/feature 確認完好）
- [x] 刪空目錄 found_matches/、'2026-01-20 195013'/、trash/；刪 miner/rl/rl_logs/（live miner/rl_logs/ 未動）
- [x] gitignore cosmetic 補充（capture 目錄 + 時間戳 glob，內容未 purge）
- [ ] （等 bot 停機）live-tree sync-conflict sweep（~3.6k 檔，在 active profile/logs 內，bot 跑時勿刪）；
      源頭：`miner/dataset/`、`playwright_profile/`、`logs/` 加進 Syncthing `.stignore`

**Phase 1 — 去重（✅ 2026-06-11 完成，commit `92c1cf8b`；審查 APPROVED，125 tests passed）**
- [x] device-id 正規化 ×2 改 `LogPaths.safe_device_id()`（manager_factory.py 已 commit；
      ⚠ new_main_v2.py 兩處同主題改動只在工作區 — 該檔混有他 session diff，待其收尾後與前置一起 commit）
- [x] `opengold_v2/ui_controller.py` 改呼叫 `sea_v2.navigator.world_to_pixel`（round() 保留、frame=(540,960)、公式等價已測）
- [x] 新 `utils/json_io.read_json_bom_safe`：json_manager/base.py 與 equipment_cache.py 讀側已導入
- [x] per-device `{ip}.json` 走 `JsonDataManager`：Mission.py load_data/record（flat schema/檔名不變）、
      fight_car.flush_logs 改 `_atomic_write_json`；new_main_v2.temporary_reset_cycles 同上 ⚠ 在工作區待 commit
- [x] 主頁 9 點像素守衛抽 `utils/main_page_guard.is_main_page_with_popup(img)`（device.py + Mission.py 兩份已換，
      profile byte-identical 驗過；battle/manager.py、park.py、tools.py 三份 inline 副本留待後續）

**Phase 2 — 效率（熱路徑；多數已完成，見 archive）**
- [x] config mtime 快取 / park sleep / 頁面 CNN inference_slot+device-aware（commit `4d2766e3`）
- [ ] `oracle()` / `get_stage` OCR 合併 → 在工作區，併入上方「前置 commit」
- [x] OCR 詞表向 `OpenGoldConfig` 收斂（✅ 2026-06-11，commits `50a7ada2`+stub fix；UNWANTED_COMBOS/SKILL_MAP/
      pair_rewrite 由 OpenGoldConfig 派生，量→暈 server alias 保留；STAGE_TEXT_REPLACEMENTS 屬 server 專用刻意不收斂）

**Phase 3 — 高風險拆分（每項獨立一輪審查；多數需停 bot 視窗）**
- [ ] `control_panel_app.py`(2902L) blueprint 漸進拆分 → **詳細計畫見下方「control_panel_app 拆分計畫」段落（2026-06-11 規劃，待使用者過目）**
      附帶：`831-835` cv2/numpy 重 import 改 lazy、刪 `943` 冗餘 flask import（各自一 commit）
- [ ] Flask 錯誤封套：抽 `_cdp_action(ip, js)` + `_cdp_err_code(err)`（先做 4 個 fire-and-forget CDP route；
      `@json_endpoint` 套 21 處延後分批、勿同 commit）
- [ ] carpark/cocos JS walker 共用化（**熱路徑**）：新 `utils/carpark_js.py` 放 `_FIND_WALKER`/`_WORLD_TO_SCREEN`；
      先重構 `cocos_navigator` 自身 4 份內部副本證明 helper，再逐點遷 carpark（path_parts 用 JS 陣列參數、
      每點先 log 新舊座標 assert 相等）；勿同 pass 動 farm_v2/sea_v2
- [ ] carpark `park_one_silver` 抽 `_reenter_silver_detail_list()`（兩分支 6 行逐字重複回復塊）
- [ ] carpark `reconcile()`：`_build_snapshot_summary` 升 module-level 純函式（勿抽純 _reconcile_plan）
- [ ] `PlaywrightGameDevice._start`(150L) 分解：先抽 `utils/web_profile_paths.py` 共用 path 正規化
      （統一採 normpath + 單測兩路徑一致），再抽 `_launch_persistent_context_with_fallback`（純 code-motion）
- [ ] V1 神燈死碼移除（gate：V2 prod log 穩定）：刪 `Open_gold_paddle_ocr.py` 4 個函式 + `__main__`、
      退休 `lian_shan_example.py`、修 stale banner/test docstring
- [ ] 舊計畫殘項：device_wrapper ~25 silent `pass` → warning log、7 個 bare `except:` → typed catch、
      抽 `poll_stage(d, target, timeout)` 共用 helper

**每 Phase 共同規則**：完成後獨立審查 + 重啟 `new_main_v2.py`；Phase 間勿混同一 commit。

### control_panel_app 拆分計畫（2026-06-11 規劃，待使用者過目後執行）

> 盤點結論：2902 行、63 條路由 + 1 條 WS、18 個 module-level 全域狀態、~20 個跨路由 helper。
> 對外相容面：`new_main_v2.py` 用 `app`/`run_server`；tests 用 `app.test_client()` + monkeypatch `_FLY_PET_ICON_DIR`。
> **原則：path 全部不變、`control_panel_app.app` / `run_server` 介面不變（外觀 façade），測試零改動即綠。**

**目標結構**

```
control_panel_app.py            # façade：建 app、註冊 blueprints、run_server、re-export 測試用符號
control_panel/
├── __init__.py
├── shared/
│   ├── cdp.py                  # _cdp_evaluate / _cdp_json_response / _FLY_PET_LOCK_JS
│   ├── command_queue.py        # _remote_commands/_global_commands/_worker_webhook_endpoints
│   │                           #   + _commands_lock(RLock) + queue_command/_is_local_command_target
│   │                           #   + _push_to_worker_webhook/_push_remote_command_if_possible
│   └── auth.py                 # _fly_pet_auth + _FLY_PET_USERS
├── routes_pages.py             # / /updates /war-room* /fly-pet 登入登出頁 + frontend_version/bug_feedback
├── routes_status.py            # /api/status /device_data /daily_progress /analyze_stage + check_ocr_server
├── routes_control.py           # pause/resume/skip_sleep/wake_delay/manual_release/force_sleep/recover
├── routes_config.py            # /api/config /api/ocr_config
├── routes_worker.py            # poll_commands/report_status/register_device/refresh_devices
├── routes_web_session.py       # web_login*/web_backup_state/web_launch/web_close + _run_web_login_worker(287L)
├── routes_live_view.py         # /ws/live_view + /api/live_view/*/stop（_live_view_sessions/_live_view_lock）
├── routes_labeler.py           # labeler/trainer 全部路由 + worker threads + _LineLogWriter
└── routes_fly_pet.py           # 17 條 /api/fly_pet_*（~750L，最大群）+ /api/fly_pet_icon + cdp_evaluate
```

**關鍵風險與對策**

1. **全域狀態唯一性**：`_commands_lock` 與三個 command dict 被 worker 路由 + control 路由共用。
   拆分後**必須只存在一份**（shared/command_queue.py 是唯一真相），blueprint 一律 `from ... import` 模組
   再以 `module.attr` 存取（不要 `from x import dict` 後重新賦值）。
2. **flask-sock**：`Sock(app)` 必須在 app 建好後初始化，`/ws/live_view` 路由註冊要在 façade
   或 `routes_live_view.init(sock)` 內延後綁定；flask_sock 缺席時優雅降級（現行為保留）。
3. **測試 monkeypatch 面**：`_FLY_PET_ICON_DIR` 等符號要在 `control_panel_app.py` re-export，
   且 icon 路由讀取時要透過模組屬性（不可在 import 時凍結成 local），否則 monkeypatch 失效。
4. **無 factory**：維持 module-level `app`（new_main_v2 直接 import）；不引入 create_app，降低改動面。
5. **bot 在跑**：每步落地後需重啟 master/worker 才生效；安排在停機窗執行。

**執行紀錄（2026-06-11 實作完成，7 個 Opus subagents 平行抽取 + façade 整合）**

- [x] P3-CP-0 `control_panel/` 骨架 + shared/cdp.py、shared/auth.py、shared/command_queue.py
- [x] P3-CP-1 fly_pet blueprint（840L；test_fly_pet_template/breed_template 的 CONTROL_PANEL 路徑跟著指向新檔）
- [x] P3-CP-2 labeler/trainer blueprint（435L，自含）
- [x] P3-CP-3 web_session blueprint（585L，含 _run_web_login_worker）
- [x] P3-CP-4 worker_sync blueprint（225L）
- [x] P3-CP-5 control blueprint（84L）
- [x] P3-CP-6 live_view blueprint（161L；WS 路由經 `init_ws(sock)` 延後綁定）
- [x] P3-CP-7 pages/status/config blueprints（142/327/53L）；façade 縮到 139L
- [ ] P3-CP-8 （待停機窗）重啟 master+worker、dashboard 全功能 live smoke（狀態頁/暫停/飛寵頁/live view）
- [ ] （未 commit）等挖礦 WS session 收尾、工作區 diff 解纏後 commit

**Review（2026-06-11）**
- 80 tests 全綠（fly_pet 全系列 + smoke_config + worker_routes + pause_routing + register_disabled
  + dashboard_template + bootstrap_api_services）。
- 路由 parity 驗證：63+2 條 path 與 HEAD 完全一致（含 /updates/、/war-room/ trailing-slash 變體與
  /ws/live_view/<ip>）；`_FLY_PET_ICON_DIR` 指 repo 根 static/flypet_icons 正確。
- 晚綁定面已驗：`_cdp_json_response`/`_FLY_PET_ICON_DIR`/`_push_to_worker_webhook`/
  `_push_remote_command_if_possible`/`_run_web_login_worker` 全部走 façade 屬性查找，monkeypatch 生效。
- façade 額外 re-export `requests`（tests monkeypatch `cpa.requests.post`）。
- 順手修一個**既有**跨檔測試汙染（與本拆分無關）：`test_bootstrap_api_services` 收集期塞 stub
  `worker_webhook_api` 害 `test_worker_webhook_applies_wake_delay`（掉線判離線 pending diff 新增的測試）
  import 到 no-op stub → 該測試現在 import 前用 `monkeypatch.delitem` 暫時逐出 stub（測後自動還原）。
- 附帶項（lazy cv2 import、冗餘 flask import）：原 943 行冗餘 import 已隨 façade 重寫自然消失；
  cv2/numpy module-level import 照原樣搬進 routes_status（保持 parity，lazy 化另案）。

### 其他既有待辦（檔案各自追蹤）

- `tasks/ws_token_home_todo.md` — 家園三件（守護靈/加工坊/伴侶），feat/ws-token-home 未 merge
- `tasks/carpark_adb.md` / `tasks/carpark_skip_silver.md` / `tasks/mount_sprint_todo.md` /
  `tasks/sea_v2_todo.md` / `tasks/mining_ore_ab.md`

---

## 工作慣例提醒

- TDD；subagents 一律 model:"opus"；計畫/進度走本檔。
- 動 live bot（new_main_v2/device_wrapper/排程）先把 plan 寫此檔給使用者過目。
- 改完 runtime 檔必提醒重啟（sys.modules cache）。

## 2026-06-12 couple 送花批次調整 (fc65396d 觀察)
- [ ] _GIFT_BATCH 20 -> 10（使用者指定預設一次送 10）
- [ ] give_all_in_hand 遇 0x0201 code=3 物品不足時降批次重試（10→5→2→1），num=1 仍不足才結束
- [ ] 更新 tests/test_ws_token_couple.py 對應測試
- [ ] 跑 focused pytest 驗證
- [x] 全部完成：couple 批次 10 + code=3 降批次 (10→5→2→1)；tests 77 passed
- [x] bot_config.json fc65396d ws_token.open_lamp false→true（使用者預期 WS 開燈）
- [x] 順手修 test_ws_token_runner _SpyClient/fixture 缺 claim_quick_2h stub（先前 idle_reward 2h 改動遺漏）

## 2026-06-12 ws_token 預設全開 + steward 副本掃蕩自動推導 + dashboard 規劃
背景: fc65396d 神祕商人(購物管家)/副本管家失效。live WS 驗證兩服務皆 ACTIVE、
遊戲內 12 章掃蕩設定都在 → 根因 (1) 裝置 ws_token.spend=false 只 read_info；
(2) ws_phase 從未把 sweep_list 接給 _run_steward(接線缺口)，且 steward 不自動推導章節。
使用者指示: ws_token.enabled 開了就代表全要 → 子功能預設全開、免逐項設定。

- [x] config_manager DEFAULT ws_token 預設改: spend=true / open_lamp=true / mining.enabled=true
      (forge_ring 維持 false: 會消耗全部真愛之石，破壞性，需明確 opt-in)
- [x] steward.py 新增 derive_sweep_list(setting): 由 dungeon_setting_info(18699) 讀到的
      遊戲內設定自動組 [(chapter, level, times)]，免手動維護章節
- [x] runner._run_steward: spend 且 caller 未給 sweep_list 時自動 derive → run_dungeon_sweep
- [x] bot_config.json fc65396d: ws_token.spend=true、mining.enabled=true(顯式 false 蓋掉新預設)
- [x] tests: test_ws_token_steward derive (48 passed) 測試 + runner/ws_phase 既有預設斷言修正
- [x] live 驗證 fc65396d: shopping shop1/8 code=0 買入(350/10件)、derive 12 章、sweep 8 章 code=0 有獎勵；571/574(購物)/576/577(掃蕩)為今日已買/票不足類非致命
- [ ] dashboard 規劃(見下)
- [ ] 提醒: bot 要重啟才生效

## 2026-06-12 萬神試煉(Beta) 協議研究（使用者：與「萬神試煉」視同一個追蹤；確認無掃蕩）

裝置：閃電 emulator-5554（CDP 9230，使用者授權）。
- [x] 入口定位：副本 tab → scrollDungeon cell「萬神試煉Beta」→ `RogueView`
- [x] 協議模組確認：**`rogue` 模組 (module 76 / 0x4C)**，與週副本 dungeon(type=23) 完全不同協議
- [x] schema 匯出：`docs/protocol/ROGUE_PROTO_SCHEMA.json`（72 messages + cmd 對照 34 條）
- [x] **無掃蕩**：34 條 cmd 全列無 sweep 類 → 使用者觀察正確
- [x] 主流程（client JS 證實）：`rogue_info(0x4c01)` → `rogue_main_enter(0x4c02){return_type}` →
      `rogue_main_combat(0x4c04){}`(server 回 seed+atk/def battle_role) → client 模擬戰鬥 →
      `rogue_main_result(0x4c05){result(0=勝,1=敗), precent=剩餘HP%}`（checkCheat 會強制改敗）→
      `rogue_main_over(0x4c03){return_type}` 結算領 p_rogue_report+rewards
- [x] 周邊系統：science=神樹祝福(0x4c16~19)、週獎勵(0x4c1a)、轉盤(0x4c13~15)、branch 分支事件(0x4c0a~12,0x4c23)、報告(0x4c08/1b/21)
- [x] live 打一場（5554 閃電）：開始→確認(btnEnsure)→進場(main_enter回入場獎勵)→開始挑戰→
      `tx 0x4c04` combat → `rx 0x4c04`(seed+atk/def) → client 模擬 → `tx 0x4c05`(result) →
      `rx 0x4c05`(reward) 一輪閉環。這關判敗(「手滑出局」)但 **server 仍接受 client 回報的 result**。
- [ ] 純 WS 可行性：result c2s 只有 {result, precent=HP%}，無 operators 回放序列 → 比 dungeon battle 簡單，
      但仍是 client 算勝負 + `checkCheat()`（client 端自我審查；server 端是否回放未知）。偽造勝利風險未知，
      需專門 live 驗（送 result=0 看 server 是否判敗/0x0201）。roguelike 多關連打不適合無腦純 WS。
- [ ] 規劃自動化路線：rogue 是 roguelike（科技樹/分支事件/逐關），自動化複雜度遠高於掃蕩。
      建議優先級低；若要做傾向 H5 emit-callback 驅動（座標/emit 都無效，須直呼 click callbackInfos）。
- 陷阱：RogueView 的按鈕 emit('click') 與 mouse.click 都無效，要直接呼叫
  `node._eventProcessor.bubblingTarget._callbackTable['click'].callbackInfos[].callback`

## 2026-06-12 dashboard 進度徽章不認 WS 完成（fc65396d 商店購買/家族任務永遠 ⏳）

根因（log 已查證，WS 本身有成功）：dashboard `/api/daily_progress`
（`control_panel/routes_status.py:211`）讀的是 `JsonDataManager` 當日紀錄
（商店購買=`Store`、家族任務=`family_market_timestamp`/`donate_family`），
這些 key 只有舊 ADB/UI 任務實作真的跑過才寫。WS 階段（`ws_token/` steward/guild）
與 `daily_pipeline._ws_skip()` 都不寫 JsonDataManager → WS 做完 → ADB 永遠跳過 →
紀錄永不落地 → 徽章永遠 ⏳。

- [x] ws_phase.py：新增 `SKIP_TO_DAILY_RECORD` + `_record_daily_done()`，WS 成功替代的任務
      回寫當日紀錄（商店購買→`Store`、家族任務→`donate_family`、挖礦→`挖礦`、萬神試煉→`萬神試煉`）。
      ⚠ 每日任務(`mission_timestamp`)刻意不回寫 — flat scalar schema（Mission.py），
      time_recording 巢狀化會破壞讀側。
- [x] TDD：tests/test_ws_phase.py 加 3 案例（成功回寫 / errored+self-skipped 不寫 /
      寫入失敗不影響 skip-set），17 passed
- [ ] 提醒：改完需重啟跑 bot 的 checkout（C:\python_project）才生效
- 順帶觀察（同台、非本題）：farm WS 每輪 `WSTimeoutError cmd=3077`；
  13:15/15:15 兩輪手機 ADB `not online` 降級純 WS

### Dashboard 規劃（WS 進階設定）
- 裝置卡「方案」選擇器旁加「WS 設定」齒輪 → 彈出 per-device ws_token 子開關:
  spend / open_lamp / mining(含 allow_bomb/allow_drill) / couple_gifts / workshop_rotate / forge_ring
- 後端: control_panel_app 既有 update_device_settings 已能寫 nested ws_token(經
  _merge_ws_token_phase_config 清洗)，只需新增 API payload 欄位白名單 + 前端表單
- 預設值顯示「預設(開)」字樣，未覆寫不寫入 bot_config(讓新預設流動)
- 改 dashboard.html + control_panel_app.py 後需重啟中控

---

## 多代理程式碼審查（2026-06-13，ultracode 6 區 + 對抗式驗證）

審查範圍：當前工作區改動（~3.7k 行 diff + ~1.7k 行新檔）。8 raw findings → 5 confirmed
（1 HIGH / 2 MEDIUM / 2 LOW），3 駁回（誤報）。全部已修 + 驗證。

### Review 結果（已修正）
- [x] HIGH `runtime_services/device_scan_service.py`：offline_fallback 手機跑純 WS 掛機時，
      仍被 ADB 缺席規則 1h 後誤判離線（違反「不放棄、不判離線」），且可能觸發 3h
      dead-device 重啟封鎖。修：`_apply_adb_absence_rule` 新增 `exempt` 參數，offline_fallback
      序號豁免並清除殘留 timestamp；call site 算一次 `fb_devices` 傳入。新增回歸測試。
- [x] MEDIUM `ws_token/carpark.py`：spill loop 中途 park 逾時拋例外 → 已停車數未經
      runner 持久化 → 配額重複計算。修：read 失敗只跳該 lot；park 例外回傳 partial
      `out`（reason=park_timeout），讓 runner 持久化已確認 parked_count。
- [x] MEDIUM `miner/v5/priors_runtime.py`：observe_scroll 取到 viewport 內(常為剛清掉=air)
      的 above cell，與離線 tape 原始地形統計語意不一致，污染 priors。修：vertical 僅在
      above 也在新揭露 band 內(`r-1>=first_new`)才計；flush/observations 改以新揭露 cell 數
      計(marginal 仍即時持久)。連動更新 4 測試 + docstring。
- [x] LOW `miner/v5/planner.py`：bottom-edge 迴圈無條件 break = 誤導性 dead structure。
      修：直接讀最後一列(行為等價，保留 unknown→dirt 預設)。
- [x] LOW `device.py`：close_notification 新增 `press("home")` 等被 try/except 吞掉
      ForceSleepRequested/WakeLoopInterrupted unwind 例外。修：三個 handler 先 re-raise
      控制流例外（top-level import，無循環依賴）。

### 駁回（驗證為誤報，不動）
- ws_token/carpark.py ext#8 `_parse_space` 解析：schema 為 repeated p_key_value，現行
  逐 field-8 解碼正確；建議的 `_parse_kv_list` 反而會丟資料。
- mining_service.py prev_board「時序錯」：實測 prev_board 與 depth_tracker._prev 同步，
  正是 scroll 對齊的參考幀；且該參數目前未被使用。
- priors_runtime.py ragged board IndexError：classifier 保證輸出 7x6 矩形，不可達。

### 待使用者決定（非本次修正）
- `miner/v5/runtime/*.json`（已存在的線上累積檔）內含舊污染語意的 vertical 計數，會持續
  以 ≤20% 上限輕微影響 merge。如要丟棄可刪除這些檔或重跑 `tools/build_v5_priors.py`。
- `tools/tmp_*.py`（5 支 scratch/probe）為未追蹤檔，建議勿 `git add -A` 進版控或加入 gitignore。

## WS 階段可被「開啟瀏覽器」中斷 + 持久化續做 (2026-06-15, worktree)

Spec: `docs/superpowers/specs/2026-06-15-ws-phase-interruptible-resume-design.md`
在 worktree `worktree-ws-phase-interruptible-resume`（base=overnight checkpoint）實作，完成後 merge 回 `feat/overnight-2026-06-14`。

- [x] `ws_token/abort.py`：`WSRunAborted(Exception)`（零相依，避免循環匯入）。
- [x] `ws_token/runner.py`：`RunReport.aborted`；`run_device(should_abort, skip_tasks)`（預設 None=不變）；`_step` 檢查 aborted/should_abort()/skip_set；`_safe` re-raise `WSRunAborted`；`should_abort` 透傳 lamp/mining。TDD：`tests/test_ws_runner_abort.py`（10）。
- [x] `ws_token/lamp.py` `open_lamp` + `ws_token/mining_supervised.py` `mine_until_pickaxe_empty`：加 `should_abort=None`，迴圈內命中即 `raise WSRunAborted`。real-raise 測試各 1。
- [x] `game_actions/ws_phase.py`：ledger（ws_resume；date+ts TTL 30min；EXEMPT={carpark,idle_reward}）→ skip_tasks；`_substantive_done`；`effective_done` 重算 pipeline-skip + farm/dungeon；abort 寫入、完整完成清空、abort 時 update_state；全程 best-effort。TDD：`tests/test_ws_phase_resume.py`（8）。
- [x] `new_main_v2.py`：WS 區塊後 `if backend=="web_h5" and has_pending_web_launch_request: continue`；init 被中斷則不快取 `pre_runtime_ws_done`。安全閘：web_h5 only（adb 無瀏覽器，避免緊迴圈）。
- [x] 驗證：focused pytest + py_compile；3 個 milestone commit。

### Review
- 三層：機制（runner，純機制；should_abort/skip_tasks 預設 None=零行為差異）、政策（ws_phase ledger）、接線（new_main_v2，web_h5-gated）。
- 安全：should_abort 與迴圈 continue 都僅 web_h5；adb（含手機fc offline_fallback）零影響。長任務（開神燈/挖礦）每批/每步讓出，已落地結果不重複。
- ledger 99% 為空（只存在於 abort→resume 之間）；TTL 30min + 完整完成清空 雙保險，避免跨喚醒誤跳 regen 任務。
- 測試限制：new_main_v2.main() 迴圈無既有 unit 測試框架且 import 全套 device/cv2 棧；兩個 guard 以 py_compile + 細讀驗證，其依賴的 primitives（has_pending_web_launch_request、run_ws_phase ledger）已全測。
- 待 live 驗證：web_h5+ws 裝置 WS 階段（mining 進行中）按「開啟瀏覽器」→ 即時開頁 → 用完重新上線續做。需重啟 master+worker 生效。

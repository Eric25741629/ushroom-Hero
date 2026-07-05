# tasks/todo.md（2026-06-20 壓縮）

> 已完成項目（含 Review）已移至 `finish.md`（2026-06-20 archive 區塊）。
> 重構真相來源：`docs/REFACTORING_OPPORTUNITIES.md`。
> 其他檔案各自追蹤：見末段。

---

## 🚧 2026-07-05 車位裝飾升級：WS 斷線續跑 + 買碎片冪等（session 44fccb3b）

背景：dashboard 一鍵升級跑到 [26/30] 北極熊 ★6→7 時 `WebSocketConnectionClosedException`
整個 job 停掉；且該步的買碎片(80萬)可能已入帳，重跑會盲買重複碎片。
慢的部分不修：每步 10s 是 skin_up 伺服器冷卻實測下限（<10s 默默丟包）。

- [x] `ws_token/carpark_decoration_ws.py`：exec 加 `target_level` 護欄（已達目標星級直接回 ok，不重升）
- [x] 同檔：買碎片冪等 — 持有碎片 = 6913 已買數 − 階梯累積消耗(排除 row0，裝飾來自免費自選)，只補買缺口；
      升級被拒且有靠推算持有時，補買差額再送一次（自癒高估）
- [x] `control_panel/routes_carpark_decorate_tools.py`：job 迴圈連線類錯誤 → 等 10s（吃冷卻+晚到）→ `ws_session.ensure` 重連 → 同步重試一次
- [x] `tests/test_carpark_ws_io.py`：新案例（target 護欄 / 跳過買 / 只買缺口 / 被拒自癒 / 斷線重連續跑 / 非連線錯誤不重試）+ 既有腳本改 level-consistent 買數；54 tests 全綠
- [x] 測試過 → merge 回 main（74c19145 / merge fb8b4dc9）→ worktree+branch 已清
- [ ] 待辦：重啟 `new_main_v2.py` 後生效（無 hot-reload）；重跑升級時中斷那步(北極熊 ★6→7, 800k)若已入帳會被冪等買折抵，不會重花
- [x] **根因修正（2026-07-05 已合併 0bb232e6）**：斷線真因＝同帳號互踢，非網路非冷卻。
      追加查證：ws_phase 觀察者閘門 11:44:17 有跑但秒放行 → 好友清單 presence **看不到純 WS
      session**（只反映遊戲客戶端在線），故互檢擋不住 dashboard 工具連線。
      修法落地：`ws_session.is_active()`（無 keep-alive 副作用）+
      `ws_phase.wait_for_dashboard_ws_release()`（15s 輪詢、開網頁請求即放行），接在
      `_run_ws_phase_for_wake` 頂端（enabled 檢查前）與 ws_runner 每輪 check_pause 後。
      已知限制（可接受，記錄備查）：① 使用者在 bot 週期進行中才啟動 job 仍可能互踢
      （job 端已有斷線重連重試兜底）；② worker 模式遠端裝置的 registry 在 master 行程，
      閘門查不到；③ bot_state 入睡清理無條件 `_pause_events[ip].set()`（~bot_state.py:583）
      未動 — 有了 registry 閘門後 pause 被吃不再致命。
- [ ] 重啟 `new_main_v2.py` 生效（兩個 fix 都要重啟）

## 🚧 2026-07-04 統一任務 due 狀態檢測（single source of truth + 精簡）— 待使用者過目

### 背景 / 動機
使用者：把每個「需要開遊戲客戶端執行」的任務的「今天/本週期 due 了沒」統一成單一狀態檢測
（`不管網頁有沒有 但我們就要統一這些狀態檢測`）。目的有二：① 消除散落重複的閘門（drift 風險，
同 `docs/REFACTOR_STATE_MANAGEMENT.md` 病根，只是這裡是 json_manager 每日狀態層，非 bot_state
runtime 訊號層）；② 為下游「client 任務全做完就不必喚醒 h5」optimization 鋪路（optional，見 Phase D）。

### 已確認事實（audit 完成）
`json_manager` 已是每日/週期狀態存儲；`should_execute_*` / `is_record_expired` / `return_time+is_next_day`
全是**純 cache 判斷、不碰客戶端**。缺的只是「單一入口」。client 任務 due-source 盤點：

| 任務 | due 邏輯現居 | 記錄名 / 判準 | 純 cache? |
|------|------------|--------------|-----------|
| 地獄之門 | pipeline inline (`daily_pipeline.py:218-230`) | `地獄之門` + is_next_day + tm_min<20 | ✅ |
| 每日加速 | execute 內 guard (`daily_tasks.py:16-21`) | `daily_acceleration` + is_next_day | ✅ |
| 競技場挑戰 | execute 內 guard (`daily_tasks.py:82-87`) | `arena_challenges` + is_next_day | ✅ |
| 坐騎衝刺 | execute 內 guard (`rank_events.py:77-109`) | `衝刺-發條` + is_mount_sprint_open + should_execute_cycle | ✅ |
| 菇菇武道會 | `_run_periodic_cycle` (`periodic_tasks.py:38-48`) | mushroom cycle + daily_limit `mushroom_arena_daily` | ✅ |
| 航海 | `_run_periodic_cycle` + `should_execute_sea_with_cooldown` | sea 日曆錨點 + 4h 冷卻 + 時段 | ✅ |
| 雲端戰鬥 | execute 內 guard (`battle/cloud.py:219-244`) | `cloud_fighting_weekly` + 週一+時+週序 | ✅ |
| 萬神試煉 | `dungeon_scheduler._run_weekly_dungeon` | 週記錄 is_next_week | ✅ |
| 雙週副本 | `dungeon_scheduler._run_biweekly_dungeon` | 雙週排程 | ✅ |
| 天梯每週 | `run_ladder_reward_if_due` | 週記錄 | ✅ |
| 菇菇雕像每週 | `run_statue_weekly_if_due` | 週五記錄 | ✅ |
| 龍骸聖域 | `run_dragon_realm_if_due`（flag 預設 off） | 三週檔期 | ✅ |
| 煩惱消 | `run_fannaoxiao_if_due`（flag 預設 off） | 每日記錄 | ✅ |
| 抽技能夥伴 | `Skill.py:7-25` 截圖紅點 | 無記錄 | ❌ 靠客戶端；有現成 WS 版 `ws_token/gacha.py` `_run_gacha_free`（`gacha_free.last_date` cache）可取代 |
| 車位戰鬥 | `run_carpark_check_if_due` | 被攻擊狀態（WS 讀） | ⚠ 非純 cache（被攻擊與否要讀狀態）→ skip 判斷保守當「due」 |

### Phase A — 建 due-registry（single source of truth，零行為變更）
- [ ] 新增 `game_actions/task_due.py`：`is_due(task: str, ip: str, now) -> bool`，內含每個 client 任務的
      純 predicate。**一律複用/抽取現有 json_manager 判斷，不重寫邏輯**。時間統一用台北 tz（UTC+8）。
- [ ] 對「due 藏在 execute 函式內」的 4 個（每日加速/競技場/坐騎衝刺/雲端戰鬥）：把 guard clause
      抽成 `task_due` 內純函式；**execute 函式改呼叫它**（消滅重複、單一真相）。
- [ ] characterization 測試 `tests/test_task_due.py`：對代表性記錄狀態，`is_due` 結果 == 目前 pipeline
      /guard 行為（先證 parity 再重構）。

### Phase B — pipeline 消費 registry（精簡；behavior-preserving，逐任務）
- [ ] `daily_pipeline.py` 各任務 inline 閘門改成 `if task_due.is_due(name, ip, now): <執行+記錄>`，
      刪掉重複的 `return_time + is_next_day` 樣板。**逐任務改、逐任務測**，不一次全換。
- [ ] 每改一個跑對應 focused test + `py_compile`。

### Phase C — 抽技能夥伴 → WS（讓 client 任務 100% cache-judgeable）
- [ ] 把 `_run_gacha_free`（已存在，`gacha_free.last_date` 每日 gate）接進走 WS 的裝置；adb 紅點版
      保留為 fallback 或退役（待定）。完成後每個 client 任務的 due 都能不開客戶端判斷。

### Phase D（使用者拍板：最強版「沒戰鬥就跳」，2 個 blocker 都消解）
決策（2026-07-04 調查後）：
- **車位**：非 blocker——WS `carpark_plan` 已自足（讀 live 停車數自校正 + 8h repark，`ws_token/carpark.py`/
  `carpark_plan.py`），瀏覽器 Task 0.5 純冗餘。→ 排除在 any_client_due 外。
- **抽技能夥伴**：使用者確認「遊戲自理、不需 bot」（呼應 lessons 2026-06-15 `free_daily=False`）。→ 排除。
- 故 any_client_due 只涵蓋 11 個戰鬥/每日客戶端任務（各按 enable flag）。

- [x] A+B+C 完成並併最新 main（branch 964ab61f，49 測綠）。
- [x] **D1 runtime**（commit 350b3548，審核 MERGE-OK）：`task_due.any_client_due(ip, now)` = OR(enabled client 任務
      predicate，13 個各 1:1 mirror pipeline enable-gate)；`bot_state.set/get_ws_login_ok`（ws_phase 只在確認登入成功才
      True，每輪 reset，無 stale）；`game_actions/browser_skip.should_skip_browser`（web_h5 ∧ toggle ∧ ws_token.enabled
      ∧ ws_login_ok ∧ ¬any_client_due）；new_main_v2 主迴圈 WS 後、喚醒前插 skip → 沿用既有對齊休眠。fail-safe：任何
      raise/不確定 → 照開瀏覽器。toggle 預設 False = 現況零改。
- [x] **D2 dashboard**（commit b4f5dad8）：per-device 開關「做完客戶端任務後純 WS 掛機」，比照 `chkWsOfflineFallback`
      backend-gated（只 h5+WS 方案顯示/可勾），`.checkbox-item` 設計系統元件；`config_manager` DEFAULT + dataclass +
      bool 轉型加 `skip_browser_when_all_done`。存檔走既有 `POST /api/config/<ip>`。

### Review（2026-07-04，A+B+C+D 完成，branch feat/task-due-unify）
- **統一達成**：14 個任務 due 判斷收斂到 `game_actions/task_due.py` 單一 registry（純函式、只讀 json_manager/
  排程/ws_state）。消費端（pipeline hellgate、dungeon 萬神/雙週、rank_events 坐騎、daily_tasks 加速/競技場、
  cloud 雲端、dragon/fannaoxiao 反向委派）改走 registry，刪掉散落重複的 `return_time+is_next_day`/週序/星期窗 樣板。
- **每階段獨立 Opus 審核**（feedback_review_before_merge_subagent_fixes）：A/B2/D1 各派獨立審核 subagent；
  B2 抓到 1 CRITICAL（hellgate 漏傳捕捉時鐘 → minute 邊界漏做），已修（d0592e57）。
- **Phase D 兩 blocker 消解**：車位=WS carpark_plan 自足（排除）；抽技能夥伴免費抽=遊戲自理不需 bot（使用者確認，排除）。
- **行為安全**：D toggle 預設 False → 現況零改；skip fail-safe 全朝「照開瀏覽器」；WS 失敗不跳（自癒刷 ticket）。
- **測試**：108 focused 綠（task_due/browser_skip/dungeon_scheduler/device_config/device_enabled_gate）。
- ⚠ **需重啟**：`new_main_v2.py`/`daily_pipeline.py`/schedulers/`config_manager.py` 皆 runtime（sys.modules cache）→
  重啟 `new_main_v2.py` + 中控才生效；dashboard template reload 即生效。
- **殘留 LOW（非阻擋）**：`_ws_login_ok` 裝置移除未清理（無害，與既有 `_web_launch_requests` 一致）；
  skip 分支複製休眠 tail（刻意，日後 tail 改需同步兩處）。

### 邊界 / 明確不做
- 不新造狀態存儲；registry 只是現有 json_manager 記錄的統一讀取入口。
- 不動 WS-skippable 任務（紅包/農場/寶箱/家族/守護靈/商店/挖礦/日常/好友禮/神燈/轉盤，已由 `ws_done` 處理）。
- 車位「被攻擊」非純 cache → 不強塞進 registry 的純 predicate；skip 判斷保守處理。
- ⚠ 動到 `daily_pipeline.py` + 各 execute 函式 = runtime，需重啟 `new_main_v2.py` 生效（sys.modules cache）。

---

## ✅ 2026-07-01 ponytail 全repo稽核 — 4 組 subagent 並行清理死碼/過度工程（完成）

背景：舊 `docs/REFACTORING_OPPORTUNITIES.md`（效率/熱路徑項）已全部驗證做完。本次是 2 個 Opus
subagent 重新稽核後的新發現，已用 grep 逐項驗證零呼叫者才列入。**執行中使用者指示加一道審核關卡**
（見 memory `feedback_review_before_merge_subagent_fixes`）：每組 worktree-isolated subagent 做完
後，先派獨立 Opus 審核 subagent 驗證 diff 才 merge，不直接信任第一個 subagent 的自我報告。

- [x] **Group 1**（死碼刪除）：刪 `utils/carpark_adb.py`+`screen_recovery.py`+`ui_layouts.py`+`ocr_clicker.py`+`model_loader.py`、`miner/ai_tuner.py`+`algo_evolver.py`+`auto_optimizer.py`+`simulator_bridge.py`、`miner/v2/llm_judge.py`+`debug_with_image_llm.py`+`tests/test_miner_v2_llm_judge.py`、`device.py` 拼錯名 `close_nofication`/`open_nofication`。**審核抓到一個錯**：原計畫誤判 `miner/v2/visualization.py` 為死碼，實際仍被 `miner/v2/debug_with_image.py`（CLAUDE.md 記載的 live debug CLI）用，已保留。Merge commit `5d6d01d7`（含解一個跟舊 backward-compat alias 相關的小 conflict）。**非阻擋跟進**：CLAUDE.md:133、`miner/v2/README.md`、`docs/INDEX.md` 等仍列被刪模組為可用 CLI，之後找時間補刪。
- [x] **Group 2**（`routes_fly_pet.py` import 收斂）：**沒有執行**。Subagent 自己發現原計畫前提錯誤——`import control_panel_app as _cpa` 不是繞路重複 import，是刻意的 late-binding，供 4 個測試檔 `monkeypatch.setattr(cpa, "_cdp_json_response", ...)` 用，改了會讓 4 個測試壞掉。維持原狀。
- [x] **Group 3**（`routes_tools_optimize.py` 拆 3 blueprint + JS 檔搬移）：拆成 `routes_carpark_decorate_tools.py`(4 routes)/`routes_gacha_tools.py`(1 route)/`routes_dragon_tools.py`(2 routes)，job registry 抽共用 `control_panel/tools_optimize_jobs.py`；`carpark_tools_js.py`/`gacha_tools_js.py` 搬進 `tools/`。**附帶發現**：repo 根目錄 `tools.py` 永久遮蔽 `tools/` 目錄成為 package（`tools/` 無 `__init__.py`），`from tools.X import Y` 全 repo 都不可行，改用 top-level import。審核逐條核對路由字串未改、job registry 真共用、`import control_panel_app` 正常。Merge commit `7776f198`。
- [x] **Group 4**（`device_wrapper.py` 小修）：刪 `click_pct`/`swipe_pct` 薄 wrapper（零呼叫者已驗）；`click()`/`tap()` 雙重 trace 改成只記一筆（下游無硬依賴 "click" 字串）。審核用 baseline 比對排除 2 個既存無關 failure。Merge commit `d73982e2`。**⚠ 需重啟 `new_main_v2.py` 才生效**（連同 Group3 動到的 control_panel 路由，中控也要重啟）。

**本批排除、未處理**：
- `device_wrapper.py` 的 `MonitoredDevice`/`PlaywrightGameDevice` 拆分（1607 行大重構，需單獨設計討論）。
- `memory/2026-06-22.md` 這個專案根目錄的雜散檔要不要刪，待使用者回覆。
- `tools/` 底下 18 個未 commit 的 probe/read/grid/nav/verify/eval 一次性腳本——原計畫要「先摘要進 docs/protocol/+memory 再刪」，這步還沒做，先留著（`tools/eval_ore_lookahead.py` 已確認要保留，不算在這 18 個內）。
- 3380 個 `*.sync-conflict-*` 裡九成在 `.claude/worktrees/*`（其他 session 的獨立 worktree），不能碰；只清了根目錄與 `tools/` 裡明確孤兒的一小批（9 個根目錄 scratch + 6 個 tools/ 內 sync-conflict 重複檔）。

---

## 🚧 2026-06-28 萬神試煉「假完成」— 偵測一次失敗就收手,實際沒打完

現象（使用者 2026-06-28）：log 寫「[萬神試煉] 戰鬥結束,共完成 6 關」+「萬神試煉:完成,
已寫入本週記錄」,但**實際沒有完成**。

證據（web-002, 06-25 23:38–23:41）：
- `battle/weekly_trials.py::_battle_loop` 偵測到第 6 關「失敗」就 `break` 收手。
- 收手當下截圖（`logs/web-002/error_screenshots/20260625_234103_..._雲端戰鬥前不在主頁面_未知.jpg`）
  顯示仍停在 rogue 關卡視圖「第55關 王者-15」,綠色「開始挑戰」還在 → **還能繼續打,但放棄了**。
- `dungeon_scheduler._run_weekly_dungeon` 在 `fought>0` 就寫本週記錄（is_next_week=False）
  → 把「假完成」鎖住一整週,下週才會再跑。

已修（上一段落,commit 146b27b9）：收尾 `_recover_to_home` 回主頁（解決「沒退出」害後續任務全跳過）。

**完成語意（使用者 2026-06-28 定案）：失敗後「退出 → 重複」,整個循環 8 次才算完成。**
不是打一局就結束,是要打 8 局。

待辦：
- [ ] 重構 `fight_test`：把 enter→`_battle_loop`(打到失敗) 包成 8 次迴圈,每局失敗後「退出」
      再重新進場,共 8 局。`_BATTLE_MAX_STAGES`/15min 上限變成「單局」上限。
- [ ] **確認「失敗→退出→重新開始一局」的精確 UI 按鈕序**（這步別猜）：
      截圖顯示失敗點「點擊」後回到關卡視圖、「開始挑戰」仍在 → 重進可能只是再按開始挑戰；
      但使用者明講有「退出」動作（舊 doc §4 有「退出→結束本局→確定」）。
      → 需 live CDP 實測 / 使用者提供精確序,才能正確實作。
- [ ] 寫本週記錄條件：改成跑滿 8 局（或盡力跑完）才寫,避免提早鎖一週（目前打一關就寫）。
- [ ] 截圖左上「13❤」是否為剩餘挑戰次數/生命？確認是否與「8 次」相關。

參考：`docs/ROGUE_WANSHEN_BETA_AUTOMATION.md`（協議 + §4 舊流程有退出/結束本局）、記憶 [[reference_ws_hellgate_protocol]]。

### 🔬 2026-06-29（週一 rogue 重置）live recon 結果（5554，read-only，已完成）
完整記錄在 `docs/ROGUE_WANSHEN_BETA_AUTOMATION.md` §9。重點：
- [x] manual-hold + CDP 9230 attach（scratchpad `rogue_recon.py`）。
- [x] WS hook + 場景樹全 dump：完整節點路徑見 §9.1。
- [x] **「13❤」=「試煉之心」(局內生命)**：勝一關不扣（16→16），推測敗北才扣（自然敗未驗）。古銀幣=金幣計數。「0/10」=獎勵里程碑非次數。未發現硬性開局次數上限。
- [x] 勝利循環 + 退出結算全程 WS 抓齊（enter 0x4c02 / combat 0x4c04 seed / result 0x4c05 client回報 / over 0x4c03），欄位見 §9.4。勝利→關卡+1 自動續。
- [x] **重大發現**：rogue 兩個確認窗「是否確認開啟新一局試煉 / 是否確認結算本局」掛在 **`TopView/MessageView`，不在 NormalView** → 任何只看 NormalView 的狀態判斷會漏看；OCR 點「開始」回 True 但卡住的根因很可能就是沒處理這個 TopView 確認窗（實測重現）。見 §9.2。
- [x] **grind 到自然敗北已驗**（2026-06-29，5554）：敗北窗=同一 `RogueBattleResultView` 藍色「失敗」+「失去 ❤1」；**敗北扣 1 試煉之心、停同關重試、run 不結束**；**❤(試煉之心)=0 才結束一局**。判勝負用 `result(0x4c05)` s2c `{2:is_win}`，不靠 OCR。
- [x] **「8 局」語意（使用者定案）**：=使用者選定的每週開局目標（非遊戲硬限）。一局=開始→爬到 ❤=0/主動結束→結算；跑滿 8 局才寫週記錄。

> **2026-06-28 待辦的 4 個「別猜」項已全解**（見 docs §9）：① 8 局=8 runs；② 失敗→退出→重進 UI 序已抓（敗不退出而是續打到 ❤=0，「退出」=右下紅箭頭→結束本局→確認窗確定）；③ 寫週記錄=跑滿 8 局；④「13❤」=試煉之心(局內生命)。
> ✅ **重寫已完成（2026-06-29，commit 827312ab，main）**：`fight_test(d, rounds=8)` 8-局迴圈（敗不停整任務、每局結束本局重進、跑滿才回 True）；`_settle_run` 退出序；`wanshen_rounds` config(1-50,預設8)+ dashboard 局數輸入；`dungeon_scheduler` 讀 config 傳 rounds + 跑滿才 time_recording；30 tests 綠。⚠ **動了 runtime（weekly_trials/dungeon_scheduler/config_manager），需重啟 new_main_v2 生效**。座標（紅箭頭510,920 / 報告✕270,875 / 等待4.5s）為實測值，首次上線可 live 微調。

---

## 🚧 2026-06-26 龍骸 SOS 救援按鈕（dashboard，純 WS 自動協助）

需求（使用者 2026-06-26）：手機在龍骸踩陷阱求助時，dashboard 按一顆「SOS」→ 指定的協助號
用純 WS 上線、自動協助所有 pending 求助隊友。**每台 web_h5 裝置一顆按鈕、只在龍骸開放時段顯示，
使用者自己挑哪台當協助號。** 動態讀求助清單救所有 pending（不寫死）。

協議（2026-06-26 live-verified，5554 CDP 抓 + 純 WS 實測救援成功，使用者手機確認）：
- `help_event_list_c2s/s2c = 0x4F15 (20245)`；c2s body 空；s2c = {event_list: repeated p_dragon_realm_event #1, help_hp:u32 #2}
- `p_dragon_realm_event = {id:u64 #1, event_id:u32 #2, role_id:u64 #3, status:u32 #4(0=pending), data:repeated #5}`
- `provide_help_c2s = 0x4F14 (20244) = {help_target:u64 #1, event_id:u32 #2}`；fire-and-forget，重讀清單確認
- 重用 `control_panel/ws_session`（ensure→get_client→disconnect；內建暫停 bot/踢線/sweeper）
- `codec.walk()` 保留 repeated（多事件）；`walk_dict` last-wins 只能解單欄

- [x] TDD `tests/test_dragon_sos.py`：rescue_pending（fake client，2 事件1pending→只對 pending 送 0x4F14、重讀回報）；is_dragon_open（注入時間）— 11 測過
- [x] `ws_token/dragon_sos.py`：`read_help_list` / `rescue_pending(client)` / `rescue_via_ws(ip, session=ws_session)`
- [x] `game_actions/dragon_realm_scheduler.py`：加公開 `is_dragon_open(now=None)`（純加，不動現有）
- [x] `control_panel/routes_dragon_sos.py`：`POST /api/dragon_sos/<ip>` + `GET /api/dragon_sos/status`；註冊進 control_panel_app
- [x] `templates/dashboard.html`：每 web_h5 列加 SOS 鈕（status.open 才顯示）+ 點擊 POST + toast
- [x] `read_help_list` 對真實 0x4F15 s2c 驗證（help_hp=2 正確、空清單不 crash）
- [ ] 重啟 control panel live 驗證整顆按鈕（協議+解析器+provide_help 皆已實證；剩 dashboard plumbing。龍骸窗口今晚 22:00 關，下次約 3 週後）

---

## 🚧 2026-06-26 龍骸 WS 卡 CAVE → 加 dead-loop 偵測（防禦性修復）

現象（5554 CDP 排錯）：`ws_token/dragon_realm.py` 的 `run()` 卡在 CAVE 神秘洞穴
（event_id=16, type=6, event_data=[]空），對它狂送 200 次 advance 全無效 →
`budget_exhausted, keys=0`（06-26 10:00/12:00 兩次）。`waits=0` 所以無偵測攔住。
CDP 驗證：瀏覽器 netManager 送同樣 event_choice(1,uid) **能推進**（eid 16→0），
driver 順暢推到三層。精確機制（ws_token socket 為何對 CAVE choice 無效、瀏覽器有效）
**未定位**，CAVE 已推掉無法即時重現，待下次 CAVE 用 ws_token client 抓對比。

防禦性修復（不需重現 CAVE，治標止血）：
- [ ] `run()` 加 state-signature dead-loop 偵測：連續 `max_stuck`(預設6) 次
      `(ceng,hp,eid,euid)` 完全不變 → return `"deadloop"`，避免 200 次空轉
- [ ] TDD：StuckClient（info 固定不變）→ 應回 deadloop 且 send < 20 次；
      FakeServer（explore/choice 正常改狀態）→ 不誤判，最終 out_of_stamina
- [ ] 7fe98fc6（小寶）等遊戲更新後也測（最多第二層，不進三層）

---

## 🚧 2026-06-26 rogue 週積分 timeout bug + WS 階段 log 補原因

問題:`emulator-5560` 每週五 WS `errors=['rogue']`。根因:萬神試煉週積分(cmd 19482)事件
休眠時 server 不回任何 frame(非 0x0201),撞滿 15s call timeout → WSTimeoutError → 被記成
task error;成功才寫日期標記,故每個週五每小時喚醒都重撞。與 guild 尋寶「休眠不回應」同型
(guild 已 except WSTimeoutError + 短探測,rogue 漏了)。Log:錯誤原因其實已逐筆寫進裝置
main.log(ws_phase.py:564 WARNING),只有 summary 行只列任務名沒原因 → 補上即可(範圍=最小高效,
使用者 2026-06-26 選定)。

- [x] 研究根因(rogue.py / runner.py / ws_phase.py + 實際 log 比對)
- [x] TDD:`test_run_rogue_dormant_timeout_is_benign`(timeout→休眠跳過/不進 errors/短探測/不寫標記)→ red→green
- [x] runner.py `_run_rogue` catch WSTimeoutError 當休眠跳過 + `_ROGUE_PROBE_S=6.0` 短探測
- [x] runner.py / rogue.py docstring 修正「失敗=0x0201」錯誤假設
- [x] ws_phase.py summary:`list(report.errors)` → `dict(report.errors)`(帶原因)
- [x] focused tests + py_compile → green(rogue 5 例綠 + ws_phase/ws_ok_summary/rogue 全綠)

### Review
- 根因:萬神試煉週積分(cmd 19482)休眠時 server 不回任何 frame(非 0x0201)→ 15s timeout
  → errors['rogue'],且成功才寫標記 → 每週五每小時重撞。修法同 guild 尋寶:catch
  WSTimeoutError 當休眠跳過 + 6s 短探測,不寫標記(事件開了下個 Friday 仍領)。
- Log:錯誤原因本來就逐筆寫在裝置 main.log(ws_phase.py:564 WARNING);只補 summary
  改帶原因 dict。範圍=最小高效(使用者選),未做 traceback 導流/JSONL。
- 既有 6 個 test_ws_token_runner.py 失敗(order/整合測試 `_SpyClient` 無 call_for、
  TASK_ORDER 含 dragon_realm/sea_season)→ 已用 git stash 在乾淨 HEAD 重現,證實是
  既有 WIP(carpark/dragon_realm/sea_season 重構)留下的 stale test,非本次造成,未動。
- ⚠ commit:runner.py / ws_phase.py 本來就帶他人 WIP(M),未自動 commit 以免綁進
  未完成重構;rogue.py / 測試 / todo.md 為乾淨隔離。等使用者定奪 commit 方式。

---

## 🚧 2026-06-25 統一在線保護（全裝置共用，使用者拍板）

問題：所有帳號都是真人，但「啟動前查帳號在不在線、在線就讓位」目前只有 5558（唯一設
`online_check_target_pid`）真的會被擋；其餘 emulator 喚醒到點直接啟動 → 異地登入踢真人。
且同一件事散成 4~5 套：`web_session_service` gate / `wake_up_handler` requester loop /
`ws_runner._protected_player_online`（舊 target_pid 跨檢）+ `ws_phase._wait_until_human_offline`
（新 snapshot 直讀，僅 human_played）+ `online_monitor`。「一套那邊一套」。

設計（best-effort 輪替偵測；對話 2026-06-25 拍板）：
- [x] **Phase 1 單一 roleId 解析器** `config_manager.get_device_role_id(device)`：顯式 target_pid
      優先，否則 creds.role_id，都沒有回 None。消滅 routes_status/web_session/wake_up/ws_runner/
      ws_phase 五處重複解析（順帶修 routes_status 5558 badge）。commit 3ce4dd68。
- [x] **Phase 2 所有裝置啟動前查自己 roleId 在線**（不只 5558）：`ws_phase._wait_until_human_offline`
      從「只 human_played」放寬到全裝置，吃 `online_monitor.account_online` snapshot 直讀（WS 登入
      是最早的踢人點，擋在這裡就涵蓋整輪）。commit ac052aa7。
- [x] **Gate 特例**：裝置若正是當前健康偵測器 → 直接啟動（monitor 連著它=不可能有真人）。
- [x] **Fail-safe**：確認在線→等；查不到（None）→ human_played 無限等（沿用 2026-06-25），
      emulator best-effort 重查 3 輪後放行。可被「開啟網頁」中斷。
- [x] **Phase 3 偵測器政策**（`online_monitor`）：起點 5554；只連 snapshot 確認離線的帳號；黏著
      （不跳回 5554）；只在(異地登入/斷線)或(自己 `next_wake_at` 進提前量 120s)時交接；交接對象
      挑「離線+休眠+短期不喚醒」；**永遠排除 5558 及所有無 creds 裝置**。commit e066c900。
- [x] 測試：128 例綠（test_device_role_id 4 + test_ws_human_offline_gate +5 + test_online_monitor +5
      + 既有 gate/phase/runner/decoupled/config 回歸全綠）。
- ⚠ 改完需重啟 `new_main_v2.py`（無 hot-reload）才生效。

調和結果（流程圖確認後）：
- None fail-safe：human_played 手機維持無限等；emulator best-effort 有限放行（已實作）。
- `online_check_service` 一次性 WS fallback：保留（mailbox 已 snapshot-first，blast radius 較小）。

未做（已知，影響小，待使用者決定是否要收）：
- gate 1（web_session `wait_for_checker_gate_before_start`）對 5558 仍以舊 fail-safe「查不到→無限等」
  跑。5558 是 ws_token.enabled → ws_phase 已守門，gate 1 變冗餘；但它在 ws_phase 之前，冷啟動且
  monitor 全盲時 5558 可能卡在 gate 1。常見情況（monitor 有 snapshot）會拿到確定答覆、不卡。
  要徹底收：gate 1 對 ws-enabled 裝置不啟動 + 處理 gate2 的 skip_online_check_once 連動，較大改動。
- gate 2（wake_up requester loop）同理冗餘；目前靠 `skip_online_check_once` 抑制，未動。

Review：
- 範圍：使用者整個 fleet 是 web_h5 + ws_token.enabled → 全部走 ws_phase 守門，已受保護。
- 安全性：穩定態（monitor 健康）零踢人——偵測器只連已確認離線帳號、bot 只在確認離線或自己就是
  偵測器時才登入；殘留只剩冷啟動盲區（使用者已接受）。
- 風險窗：gate 1 對 5558 的舊 fail-safe（見上）；偵測器 snapshot 過期 60s 內的競態（極窄）。

---

## 🔧 2026-06-25 human_played 裝置:自己的 WS 階段別把真人踢下線

問題:`adb-fc65396d`(手機,`human_played=True`、`ws_token.enabled=True`)每次喚醒先跑 WS 階段,
WS 登入「會踢同帳號其他 session」(`ws_phase.py:4`),真人正在手機上玩時就被自己的腳本異地登入踢掉。
`human_played` guard 目前只接到 online_monitor(偵測器不拿真人帳號登入),**沒接到裝置自己的 WS 階段**。

設計(使用者 2026-06-25 拍板:維持偵測器自動輪換;觀察者看不到該帳號時當作可能在線、繼續等):
- [x] `ws_token/online_monitor.py`:加純讀函式 `account_online(role_id, *, max_age_sec=60, now=)`
      (快照存在且 ≤60s 新鮮 → 回該 roleId 的 online;否則/不在好友清單 → None)。
- [x] `game_actions/ws_phase.py`:`run_ws_phase` 對 `human_played` 裝置,在 bootstrap/登入前
      先 `_wait_until_human_offline(ip)`:`False` 才放行;`True`(在玩)或 `None`(看不到)
      都每 30s 重查直到 `False`。無 creds(解不出 roleId)→ 直接放行(本來就登不進、踢不了人)。
      涵蓋正常 WS(`new_main_v2.py:284`)與離線 fallback(`:207`)兩條路(都走 `run_ws_phase`)。
      間接層 `_account_role_id` / `_account_online`(tests monkeypatch 用)。
- [x] 不動 wake loop 核心、不動偵測器選擇邏輯、不 pin 觀察者(維持現狀:preferred=5554、忙了輪換)。
- [x] 測試 `tests/test_ws_human_offline_gate.py`(12 例):online→等待、offline→放行、None→等待、
      無creds→放行、human_played 才檢查/bot 裝置不檢查、account_online 新鮮/過期/缺/不在好友清單。
- 觀察者現況(實測):detector=5554「閃電」、追蹤 50 好友、看得到手機(account_online=False)。
- ⚠ 改完需重啟 `new_main_v2.py` 才生效(無 hot-reload)。
- ponytail 上限:真人連玩數小時 → 該裝置整段 park(每 30s 重查),不設硬上限,符合「直到下線」。

Review:
- 全綠:新 12 例 + `test_ws_phase.py` 47 例 + `test_online_monitor.py`/`test_config_human_played.py` 11 例。
- 範圍:只擋「裝置自己的 WS 登入」(異地登入踢人的唯一來源)。ADB UI 任務同客戶端不會踢人;
  且閘門在 WS 階段(ADB 任務之後)→ 真人在玩時整輪自然延後,等下線才跑,順帶不搶螢幕。
- 邊界:觀察者輪換到非好友帳號 → 該帳號 None → 當可能在線繼續等(使用者指定);最常見情況
  5554 閒置當 detector、看得到手機 → 不誤等。
- 未動:`bot_config.json` 不需改;`new_main_v2.py` 不需改(閘門全在 `run_ws_phase` 內)。

## 🔧 2026-06-24 航海/龍骸聖域 dashboard 燈接線 + 航海改日曆錨點

問題:dashboard 每日進度的「航海」燈不亮、龍骸聖域形同沒接。根因:
- 航海檔期判斷用 `sea_cycle_start`(各裝置「上次跑」時間)推 4 週 → 隨 run 歷史漂移、與遊戲檔期脫鉤、各裝置不一致。本週(06-22)實際開航海,但磁碟 `sea_cycle_start=06-17` 讓它算 off-cycle → badge 被藏 + run 排程也不會跑航海。
- 點亮用 `check_is_today`:多週活動只在跑的當天亮,隔天又變 ⏳。

修法(run 排程 + dashboard 共用日曆錨點,全裝置一致):
- [x] `json_manager/scheduling.py`:新增 `is_sea_week()`(錨點週一 2026-06-22、28 天一輪,與龍骸 `_is_dragon_week` 同形);`should_execute_sea_with_cooldown` 改吃 `is_sea_week`,丟掉 `sea_cycle_start` 週期數學(保留時段視窗 + 4h 冷卻)。
- [x] `json_manager/__init__.py`:export `is_sea_week`。
- [x] `control_panel/routes_status.py` `daily_progress`:航海 gate 改 `sea_week`(日曆)、龍骸維持 `triweekly`;兩者點亮改 `period="week"` → `manager.is_same_week`(檔期週整週維持 ✅)。
- [x] 測試:`tests/test_sea_week_anchor.py`(is_sea_week + run gate 7 例)、`test_daily_progress_badge.py` 補 `is_same_week` + week-predicate 例。

Review:
- 全綠:本批 18 例 + `test_json_manager.py` 85 例(既有航海視窗測試用 05-29=錨點前整 4 週,仍判為航海週,不受影響)。
- 只在檔期顯示(沿用隱藏 gate),非檔期週仍不顯示。
- ⚠ 需重啟 `new_main_v2.py` + 中控才生效(無 hot-reload)。
- 範圍外但同雷:`periodic_tasks.should_execute_sea` / `week_events`(legacy,daily_pipeline 未用)、武道會/坐騎衝刺 badge 仍用 today-predicate + cycle-from-last-run,未動。
- 錨點若與遊戲實際檔期不符,只改 `_SEA_ANCHOR_MONDAY` / `_SEA_CYCLE_DAYS` 兩常數。

### code-review 跟進(xhigh workflow,57 agents)
- [x] #1 時鐘分裂(CONFIRMED):dashboard 龍骸閘用 naive 本地、點亮用台北 → 抽 `_compute_daily_progress(manager, device_id, today=)`,把台北 today 同餵 `is_sea_week`/`_is_dragon_week`。(UTC+8 主機本無影響,屬 latent)
- [x] #2/#3 覆蓋缺口:`get_daily_progress` 抽成可測純函式,新增 4 例真實端對端(航海週顯示+點亮/本週非今天仍亮/非檔期隱藏/龍骸週);移除原套套邏輯測試。
- [x] #4 視窗測試巧合耦合:`TestShouldExecuteSeaWindow._at` 改由 `_SEA_ANCHOR_MONDAY` 推算 + precondition assert。
- [x] #5 wall-clock flaky:`_FakeManager` 加 `now` 注入。
- [ ] 未改(記錄):#8 `is_sea_week` 與 `_is_dragon_week` DRY 重複(可抽 `is_calendar_cycle_week`,但動到龍骸 run-gate TZ,verifier 判 pure-style);#6 `is_same_week` 少 flat-scalar 容錯(sea/dragon 紀錄恆 dict,低風險);#7 `week_events` legacy 仍用舊漂移錨(未 wired live);#9 dead `cycle_weeks` 參數;#10 sea_week/period 旗標冗餘。

---

## 🔧 2026-06-24 web_h5 WS 憑證初次種子自動化

問題:5558(web_h5)WS 階段一直噴「no captured creds」→ fallback Playwright。根因:
- `refresh_from_device`(Playwright 回寫)只刷新既有 capture,缺檔即跳過(page 讀不到 uname/plat,湊不出第一份)。
- `bootstrap_token`(冷啟 App 撈 logcat)只對 adb 生效(`_should_bootstrap` 寫死 backend=="adb")。
- 故 web_h5 缺「第一份種子」的自動路徑;其他 web_h5 模擬器是當初 adb_token_login 手動種的。

修法(使用者核准):
- [x] `game_actions/ws_phase.py`:加 `_should_seed_web_h5(ip, backend, cfg)` = web_h5 + bootstrap_token 旗標 + **缺 capture**(`_has_ws_creds`) + **adb 可達**(`_adb_reachable`,複用 `ws_runner_service._is_adb_reachable`)。`run_ws_phase` 在 adb bootstrap 後加 best-effort 種子步驟:成功本輪續跑 WS,失敗只 log → 降級 Playwright(行為同舊)。種完 has_creds 為真 → 不再冷啟。
- [x] 純雲端 web(web-xxx 不在 adb devices)自然被排除,免每輪空跑 adb_token_login。
- [x] 測試:`tests/test_ws_phase.py` +4(不可達不種/缺檔可達種/有檔不種/旗標關不種);全檔 49 綠。

ponytail 上限:缺 App 但 adb 可達的 web_h5 會每輪重試(~2min);模擬器實務上都有 App,種一次即止 → 暫不加退避。
⚠ 需重啟 `new_main_v2.py` 生效;之後 5558 下一輪 WS 會自動種子,不必再手動 adb_token_login。

---

## ✅ log 確認已 LIVE（bot 自 2026-06-19 21:57 起跑此 checkout，2026-06-20 log 核對）

下列已在 5 台（5554/5556/5560/小寶 7fe98fc6/手機 fc）log 確認實際執行、無 error：
- WS 全 pipeline 每輪跑完（ok 含 carpark/main_tasks/farm/dungeon/relic/relic_sprint/mining/lamp/... ）
- carpark_plan 跨界停車 + 倉庫收益領取（carpark task + carpark_scheduler 車位檢查）
- relic_sprint（每輪 clean 完成，無 error；但 spend 細節未入 log → #25 仍待驗）
- 農場 WS：read_work_status、buy 407/408 to daily target、豐收卡循環「開始」+ ws_farm.log 落檔（#22）
- **offline_fallback（手機 fc）**：「手機 ADB 不可達，啟用離線純 WS 備援」→ 整輪 WS 跑完，多次重複 OK = #6 LIVE 驗證 ✅
- **WS 挖礦 hold_floor deadlock 修**：pickaxe 實際遞減（8→7→6）、盤面變動、unconfirmed 自動換格續挖、無卡死 = LIVE 驗證 ✅
- token bootstrap：手機 fc 走 WS 備援皆成功（token 可用）

## ⚠ 仍需「再次重啟」才生效（on-disk 已超前 running process）

running 進程是 06-19 21:57 那版；之後 commit 的改動尚未套用。log 鐵證：
- **ad_reward 完全沒在 log 出現**，但 on-disk 已 wired（runner.py:84 TASK_ORDER「ad_rewards」）+ bot_config 5 台已開（L152/301/472/616/773）→ **running 版的 TASK_ORDER 沒有它，需重啟才會跑**（#15 卡在這）。
- 豐收卡循環只 log「開始」，缺 on-disk 的 `[1/7]..[7/7]` 詳細步驟 log（running farm.py 較舊；循環本身仍有跑，farm task=ok）。
- 其餘待套用：工具面板/遺物升級工具（中控 routes）、徽章 WS 回寫（#19）、online_guard WS online-check（需 `online_check_via_ws:true`）、control_panel 拆分 smoke（P3-CP-8）、遺物本期活動結束日 end_ts。

**動作**：重啟 master(infinite)+worker(desktop_ov0asq4) `new_main_v2.py` + 中控 → ad_reward 才會 live；之後即可驗 #15/#25。

**待 commit**：2026-06-19 夜間批次（237 測試綠，commit hold 原因已解除）。
純新檔（ad_reward.py / relic_sprint.py / routes_ad_reward.py / routes_relic_sprint.py + 各新測試）可單獨乾淨 commit；
共用檔（config_manager / bot_config.json / logging_utils / log_paths / ws_phase / daily_pipeline）混工作區，待使用者過目全 diff 再決定範圍。

---

## 進行中 / 待辦（live 驗證 + config 釘值）

### 手機fc 純 WS 停車
- [ ] #13 開窗（台灣 10:00-22:00）後跑 `python tools/carpark_cluster_probe.py` 採樣占用者 attrs，確認同服欄位 id；若 kv 不含 server_id 要修 `count_same_server`。

### offline_fallback / token bootstrap
- [x] offline_fallback：log 已確認 LIVE（手機 fc 多次「ADB 不可達→WS 備援→整輪跑完」，2026-06-20）。
- [ ] token bootstrap：fc 走 WS 備援皆成功（token 可用），但「creds 缺失時 refresh→force-stop」這條 bootstrap 路徑本身未單獨在 log 看到觸發（目前都已有 creds）。低優先。⚠ fc 與 5554 同帳號，WS 登入互踢，兩台同開 ws 需錯開。

### WS 後端整合（程式零殘項，待 live 釘 config 值）
- [ ] S7 5556 pilot live 驗證（撈 ticket → 跑一輪，等停機窗）。
- [ ] live 釘值（未填則該功能自動 skip）：深淵/萬神 dungeon type+id+num（掃蕩 only）、農場 seed_id/team_cfg_id、豐收卡 shop_type/id。
- [ ] #25 遺物衝刺 live 驗：count 單位（碎片量 vs 次數）、4 輪精確門檻、當期 act_type(13/269)、領獎回應形式。若 count=次數，run_relic_sprint 的 accrued 扣抵邏輯要改。協議 `docs/protocol/RELIC_SPRINT_RECON.md`。

### Dashboard 設定重整 — 收尾
- [ ] 重整頁面 live 驗一輪（改 template，reload 即生效，不需重啟 bot）。
- [ ] （低優先）farm/dungeon_sweeps 後端 sanitizer（目前 frontend 控型別 + runner 防呆 passthrough）。

### 菇菇雕像每週消耗（statue_weekly）
- [ ] `bot_config.json` 兩台（5554、adb-fc65396d）`statue_weekly.amount` 1 → 7000。
- [ ] 修 ADB flow `send_keys(clear=True)` 不穩（ADB_KEYBOARD_CLEAR_TEXT null ref）：改不帶 clear 直接輸入或走 `adb shell input text`（`statue_weekly.py:425` 附近）。

### v1 挖礦 follow-up（非 deadlock，屬效率）
- [ ] `mine_until` 遇首個 unconfirmed 即停 → 學 deplete_pickaxes 重讀續挖。
      註：runner 走的 `mining_supervised` 路徑 log 顯示已會在單步內換多個候選格續挖（2026-06-20，refresh_attempts 最多 9 + 換 block_id），此 follow-up 只剩 standalone `mine_until` 那條。低優先。

---

## 研究待續（未拍板）

### 地獄之門 純 WS（小寶 7fe98fc6）
協議已解（`main_chapter` 模組 13；docs/protocol/MAIN_CHAPTER_PROTO_SCHEMA.json）。使用者決策：不自造封包偷跑，先真打一場被動抓包。
- [ ] 下個 :00-:20 窗：小寶 H5(CDP 9230) → `python tools/watch_ws.py --port 9230 --seconds 800 --out logs/hellgate_capture.jsonl` → 真打一場。
- [ ] 解碼 3331 send frame 的 operators#4：非空回放=純 WS 不可行（維持 ADB/H5）；空/極簡仍 code=0=可行。
- [ ] （若可行）`ws_token/hellgate.py` + 測試 + runner/config/ws_phase/daily_pipeline 接線（注意 sleep ~10 分不可 block 其他任務）。

### 萬神試煉(Beta) rogue（module 76）
協議已解（docs/protocol/ROGUE_PROTO_SCHEMA.json，34 cmd 無掃蕩；live 打一場閉環，server 接受 client result）。
- [ ] 純 WS 可行性：result c2s 只有 {result, precent}，但 client 算勝負 + checkCheat()，偽造勝利風險未知，需專門 live 驗（送 result=0 看 server）。優先級低（roguelike 多關連打不適合純 WS）。

### 純視覺農場退役計畫（待 WS farm 三塊 live 驗後執行）
取代者：ws_token ad_reward(15) / farm.harvest_card_cycle / farm.buy(407/408)。
- [ ] 前置：5556/5560/7fe98fc6 接 WS farm 三塊並 live 驗；adb-fc65396d 確認是否接 WS（不接則 ADB/視覺路徑全留）。
- [ ] **接線缺口（退役前必修）**：`game_actions/ws_phase.py:271` 農場 skip 條件只看 `farm.seed_id`，但 WS 農場用 `farm.buy`/`harvest_card_cycle`（無 seed_id）→ 改成 harvest_card_cycle 跑成功才 skip。未修則刪視覺農場會讓農場任務無人做。
- [ ] 可刪清單（A 低風險 / B 保留 ADB 後備 / C 順手清死碼）詳見舊版本（git 歷史）；待刪處已加 `# remove-after-ws-farm-verify(2026-06-19)` 註解。**weekly_card.py 不可整檔刪**（check_if_parttime 仍被 harvest_card 用）。

### #20 主 dashboard 重設計（待使用者過目，刻意未動手）
裝置卡片設定收斂成幾個按鈕/開關。屬改 live 主控面 + config 存檔的結構性重寫，高度依賴外觀驗收。
- [ ] 盤點 dashboard.html 卡片欄位 + routes_config 存檔對應；ws_token 子開關收進摺疊「WS 任務」分頁；方案用 segmented buttons；進階欄位摺疊；分階段（先一張卡片 → 過目 → 套全部）。

### 進階設定 副本掃蕩改版（後續單獨對話）
- [ ] 用詞白話化 + 是否改逐欄表單取代 JSON。本次重整不碰。

### 降運算 Phase 2（需 live 驗證，等拍板）
- [ ] 2.1 Chrome `--disable-gpu`/headless（需驗 Cocos 畫面+OCR）；2.2 `cc.game.setFrameRate` 限 FPS（build 是否支援未驗）；2.4 `cv2.matchTemplate` 前 2× 縮圖（需重調 0.8 門檻）。
- [ ] 殘留重疊：`wake_up_handler.py:322` 舊分流延遲與 F4 啟動錯峰疊加（動時須保 5554 online-check 提前 return）。

---

## 重構 Phase 3（高風險拆分，真相來源 docs/REFACTORING_OPPORTUNITIES.md）

> 2026-07-01 稽核後更新：以下 4 項已驗證完成，從清單移除——
> `oracle()`/`get_stage` OCR 合併（config cache/inference_mode 等已在 code 裡確認）、
> carpark `_reenter_silver_detail_list()`/`reconcile()._build_snapshot_summary` 已是
> module-level 純函式、V1 神燈死碼已刪、device_wrapper silent pass/bare except 已全清（0 個）。
> `carpark/cocos JS walker 共用化` 這項本次稽核判定 yagni（`carpark_js.py` 現存 walker 抽象
> 用量太少，`cocos_navigator.py` 僅 5 處、`carpark_auto.py` 30 處，不值得為此立一個共用檔），
> 移除此項、不再追蹤。剩下真正還開著的：

每項獨立一輪審查 + 重啟 `new_main_v2.py`；Phase 間勿混同 commit。
- [ ] Flask 錯誤封套：抽 `_cdp_action(ip, js)` + `_cdp_err_code(err)`（先 4 個 fire-and-forget CDP route；`@json_endpoint` 套 21 處延後分批）。
- [ ] `PlaywrightGameDevice._start`(787-909, ~123行) 分解：已抽出 3 個 nested helper(`_clear_chrome_singleton_locks`/`_build_launch_kwargs`/`_launch_fn`)+`web_profile_paths`/`_launch_with_profile_recovery`，剩最後一段整併（2026-07-01 稽核判定「半部分完成，剩的價值有限」，低優先）。
- [ ] Phase 0 殘：live-tree sync-conflict sweep（~3.6k 檔，90% 在 `.claude/worktrees/*` 其他 session 的獨立 worktree 內，**不能碰**；其餘在 active profile/logs，bot 跑時勿刪；源頭加進 Syncthing `.stignore`）。2026-07-01 已清根目錄+tools/裡明確孤兒的一批，範圍很小，這項本質沒動。

---

## Review — 純WS挖礦地形重建（2026-06-20, d2a9c81c）
- [x] 釘死根因：WS 0x0c01 不送未挖格地形型別（201/202 全 count==0 已挖、僅 401 礦坑 count>0、未挖=純 active）。planner 盲填 dirt、永不對未挖石頭下炸彈 = 效率天花板。
- [x] 模型：client 把 12 個相異 7×6 模板垂直 7-列堆疊；每 band = 單一模板（CNN 28/28 + WS 22/22 雙重驗證）。
- [x] 自學：邊挖邊用已挖格 config_id 比中模板、推回未挖地形。phase=1/row 經 14 對齊中唯一無矛盾驗證（5556 帳號）。
- [x] 安全整合：未挖 active 只在重建為 STONE 時 dirt→rock，其餘不動 → 絕不退步；稀疏資料=no-op。
- [x] `ws_token/mine_terrain.py` + `mining_adapter`/`mining_supervised` 接線、per-device cache、68 測試綠、live 實測鎖定 phase 並重建 4 個 rock 格。
- [ ] 待生效：new_main_v2 重啟後上線；隨各帳號挖礦自動累積 band→template（summary log 看 `terrain=`）。
- 限制（使用者已同意自學）：只重建「已揭露足量」的 band，全未挖的遠方 band 需 client RNG seed（未做）。

## Result — 「以礦為路徑」延伸視野：實測否決（2026-06-20）
- 量過 7 列 vs 12 列（masked：下方只給礦位置、地形未知）：score +3%(1288→1328) 但**效率變差**(0.36→0.32 pit/鏟)、鏟耗↑(193→217)。raw 分靠多花鏟換、礦沒多收。
- 機制：下方只知礦位置不知土/岩 → 朝礦衝會撞到未知石頭白花鏟；且礦本來就會隨下挖捲進視野。
- 效能：v1 A* 到 14 列穩(<25ms)，17+ 偶爆 220ms。
- **結論：不做 ore-path。** pure-WS 下「每 band 進視野後即時最佳化」已是效率天花板。
- 改為補完「視野內被遮擋格用模板填」(commit a12c6144) —— 這才是真正提升每-band 最佳化的那一塊。
- 工具留 `tools/eval_ore_lookahead.py`（要更大樣本重驗可用）。

## Plan — 「以礦為路徑」延伸視野規劃（已否決，保留紀錄）
目標：planner 不再只看 7 列視窗，而是朝下方礦規劃下挖路線（效率最大化收尾）。
現況：planner v1 只吃 7 列；WS `map_pits` 給下方 ~17 列礦坑位置但只當 telemetry；未挖 band 地形未知（加權隨機）。
- [進行中] 上界實驗（零 live 風險）：`tools/eval_ore_lookahead.py` 餵 plan_smart 加高地圖（7/14/21 列，god-mode 真實 tape）量分數增益，判斷「拉長視野」值不值得做。
- [ ] 若上界有顯著增益 → 做「真實版」：地圖 = 視窗重建地形 + WS map_pits（下方礦坑）+ 已識別 band 的重建地形；未識別 band 地形以平均成本代入。只執行可達（視窗內）挖步，深層只影響下挖方向/炸彈時機。
- [ ] 在 `mining_adapter.plan` 組「加高 grid」餵 plan_smart；步驟座標映射回 block_id；補測試 + sim 評測對照 v1 baseline。
- [ ] 限制（誠實）：未挖 band 地形不可預測，下方只能「朝已知礦坑方向」最佳化，視野上限 ~17 列。
- 動 live 演算法前先讓使用者過目本 plan。

## 工作慣例提醒
- TDD；subagents 一律 model:"opus"；計畫/進度走本檔。
- 動 live bot（new_main_v2/device_wrapper/排程）先把 plan 寫此檔給使用者過目。
- 改完 runtime 檔必提醒重啟（sys.modules cache）。

## 其他既有待辦（檔案各自追蹤）
- `tasks/ws_token_home_todo.md` — 家園三件（守護靈/加工坊/伴侶），feat/ws-token-home 未 merge
- `tasks/carpark_adb.md` / `tasks/carpark_skip_silver.md` / `tasks/mount_sprint_todo.md` / `tasks/sea_v2_todo.md` / `tasks/mining_ore_ab.md`

## 萬神試煉 重寫（rogue Beta，2026-06-20 CDP 實測驗證）

### 根因
舊 `battle/weekly_trials.py::fight_test` 跑舊版 7 場 UI（開始/確定/結束本局/秘寶閣每輪），但遊戲已改成 roguelike「萬神試煉Beta」(RogueView)。OCR 子字串命中「萬神試煉」→ 入場 OK，但進場後按鈕序列全不符 → 空轉 5 分鐘 + 無條件 `time_recording` → 整週鎖死。`dungeon_scheduler.py` 排程決策原用 root `logging`（已改 per-device `logger`，2026-06-20 補 log）。

### 已實測驗證的 OCR 流程（全用內建 `img_tools.click_str_by_server`，canvas 540x960）
進場（兩種狀態都要吃）：
- 暖啟（有進行中局，如 5554）：主面板「繼續」(270,744) → 關卡視圖
- 冷啟（無局，如 小寶 7fe98fc6）：主面板「開始」(271,844) → 開局獎勵頁「開始」(270,724) → 提示「是否確認開啟新一局」→「確定」(370,553) → 「恭喜獲得」頁「進入遊戲」(380,847)〔可選「刷新」重骰〕 → 提示「是否確認進入本次萬神試煉」→「確定」(370,554) → 獎勵 toast「點擊空白處關閉」(269,606) → 關卡視圖

戰鬥迴圈（打到不能打為止）：
- `開始挑戰`(271,711) → 戰鬥 client 自動跑 ~3-7s（可選點「跳過」）→ 結果「勝利」→「點擊…關閉」→ 自動進下一關 → 重複
- dismiss 文字有變體：5554=「點擊任意位置關閉」、小寶=「點擊空白處關閉」→ 用子字串「點擊」匹配

### 進度
- [x] 重寫 `fight_test`(`battle/weekly_trials.py`)：副本→入場(萬神試煉+277,75)→`_advance_to_stage()`(繼續/開始雙路徑+確認鏈通用輪點)→`_battle_loop()`(開始挑戰→點擊關閉直到找不到開始挑戰或偵測失敗，MAX_STAGES=80，可帶 max_stages 測試限關)→`buy_god_everyweek`。回傳 bool。
- [x] success-gating：`dungeon_scheduler._run_weekly_dungeon` 只在 `fight_test` 回 True 才 `time_recording`(防失敗也鎖一週)。
- [x] 單元測試：`tests/test_dungeon_scheduler.py` 13 passed(含新增 fight 失敗→不記錄)。
- [x] live：真 `fight_test` 用 production OCR 跑通；事件關閉時優雅回 False(中止,不誤記錄)。entry+battle loop 已於週六 5554/小寶 驗證連打多關。
- 異體字非問題：opencc s2t 把「秘」「祕」都正規化→`buy_god_everyweek('秘寶閣')` 命中新 UI「祕寶閣」。

### 退出/結算流程(2026-06-21 實測)
- 右下角紅色箭頭(≈510,920，OCR 常誤判為 'G') → 提示「選擇暫時離開或結束本局」→ `結束本局` → 提示「是否確認結算本局」→ `確定` → 回副本頁。
- 對話框按鈕**吃 mouse.click**，但 dialog 轉場需 ~4-5s 等待(2.5s 太短會誤判點不動)。`暫時離開` 在事件已結束時為 no-op(只 `取消`/`結束本局` 有效)。
- 注意：`RogueView/btnClose`、`RogueEndTipsView` 按鈕的 `emit('click')` 無效(整條祖先鏈無 click listener，cc.Button 走 editor clickEvents)→ 要驅動只能座標點擊+足夠等待，別用 emit。

### 待補實測(事件只到週六 23:59:59，週日關閉；下輪週一下午開)
- [ ] 自然停止訊號 = 點掉結果窗後「開始挑戰」是否再現(勝→再現續打；敗/次數用盡→消失停)。**不辨識勝敗字**(勝/敗窗長得一樣)。待驗：真實失敗後「開始挑戰」確實消失、本局正常結束(帳號太強沒打到，下輪排程跑+看新 log 補)
- [ ] 失敗/結束時是否需在 `_battle_loop` 後補「紅箭頭→結束本局→確定」結算(目前靠自然結束)；待真實失敗路徑驗
- [ ] 結尾 `buy_god_everyweek` 在新 RogueView 後是否走得到祕寶閣(loss 後可能在結算頁非主面板)；待驗
- [ ] H5 驗證後回測 ADB(小寶手機 adb-fc65396d 純 OCR 場景)
- 探測工具：`tools/_rogue_drive.py`(步驟驅動)、`tools/_test_fight_test.py`(端到端跑真 fight_test，限關)，皆 `ROGUE_PORT` 指定埠
- ⚠ runtime 改過 `battle/weekly_trials.py` + `game_actions/dungeon_scheduler.py`，需重啟 new_main_v2 生效(sys.modules cache)

## 天梯每週獎勵 純WS自動選 (2026-06-21)

需求(使用者):打完雙章節天梯(雲纏天梯試煉)後要選每週獎勵;希望未來每週自動照同樣選、走 WS。多帳號(小寶 + 5558)。5558 若沒選滿 25 個 → 用小寶的補滿。

### 協議(live 驗證 小寶 CDP 9226)
- **cmd `0x4001` (16385) double_ladder_select**(module 64):c2s body = repeated `pick{1:難度,2:index}`;s2c echo = 已存清單 + 尾端 `{2:0}` = 成功。
- 小寶實抓:25 個 pick,難度 16~25;重放相同 bytes → echo 結尾 `1000` 成功。**冪等**(重送=重存,不會重複領)。
- 解碼:`難度25:[1,2,4] 24:[1,2] 23:[1,2,3] 22:[1,2,3,4] 21:[1,3] 20:[1,2,3] 19:[1,3] 18:[1] 17:[1,2,3] 16:[1,2]`

### 已完成(不動 bot 行為,安全)
- [x] `ws_token/ladder_reward.py`:encode/decode/merge picks + `apply_selection`(call 0x4001,err 0x0201 不炸 runner)+ 每裝置存檔 `load/save/get_body_hex/record_device`
- [x] `ws_token/data/ladder_reward.json`:小寶 `7fe98fc6` 已記錄(25 picks,source live CDP 9226)
- [x] `tests/test_ladder_reward.py`:5 passed(真實封包解碼 / encode-decode roundtrip / merge 補滿 / 多byte varint)
- 工具:`tools/probe_xiaobao_reward.py`(PROBE_PORT 指定埠;install/drain/decode/replay/state)

### 待辦
- [ ] 抓 5558(CDP,例外):開 5558 瀏覽器到天梯頁 → install 監聽 → 手動選一次 → drain 0x4001 → `merge_picks(5558, 小寶)` 補滿 → apply 套用 → record 5558
- [ ] **runner 接線(需使用者同意,動到正在跑的 bot)**:`ws_token/runner.py` 加 `_run_ladder_reward(client, device)`:gate=該裝置有 record 且 `enabled`(預設 True)+ ws_state 日期閘(每天一次,冪等;daily 閘可覆蓋日/週結算重置)→ `apply_selection`。放 free 任務群(main_tasks 後)。
- [ ] (可選)dashboard 顯示/重抓按鈕
- ⚠ 接線後需重啟 new_main_v2 生效(sys.modules cache)

### 實作完成 (2026-06-21) — 改走 CDP/頁面WS、每週二
依使用者最終指示:頻率=**每週二一次**;5558 走 CDP(無 ws_token);其他可走純ws(許可非強制)。
最小 bot 改動 → **單一 CDP 路徑**接 daily_pipeline 尾段,涵蓋兩台(小寶+5558 都 web_h5)。
- [x] `ws_token/ladder_reward.py`:純邏輯(encode/decode/merge/varint)+ store + **週二閘**(`is_due`,ISO 週 marker 去重)+ `apply_if_due(device, today, page=/client=)`(雙傳輸:page=`WebGameAPI.call_raw` CDP / client=純WS,日後小寶要純ws只需加 runner 5 行)
- [x] `game_actions/ladder_reward_weekly.py`:`run_ladder_reward_if_due(d, ip)`(web_h5 only,拿 `d._page`+today,吞例外)
- [x] `game_actions/daily_pipeline.py`:Task 14.7 尾段呼叫(try/except)
- [x] `tests/test_ladder_reward.py`:10 passed(解碼/roundtrip/merge/週二閘/marker去重/no-transport);全套 + test_daily_pipeline 17 passed
- [x] 兩台已 live 套用本週(小寶 replay echo ok、5558 套小寶模板 echo 25 ok)並記錄 enabled
- ⚠ **需重啟 new_main_v2** 自動排程才生效(sys.modules cache);本週已手動套用,下週二起自動
- 限制:body 綁帳號已達難度(16-25);若帳號升難度或改選擇 → 用 `tools/probe_xiaobao_reward.py`(PROBE_PORT)重抓 `drain`→`record_device` 更新

---

## 重寫 ws_token.farm.run_harvest_card_cycle — WS 4 階段豐收卡 (2026-06-22)

**Context**: 5554 等 WS 豐收卡一直沒效。用 `tools/ws_harvest_step.py`(CDP 接瀏覽器、單步 + sniff/capture)live 反推完整協議 + 對齊正確流程。現有 `run_harvest_card_cycle` 三個結構性問題:漏「清場」、漏「收成 3080」階段、施肥 num=1(該 3)、無 N×5 迴圈。詳見 memory `reference_ws_harvest_fertilize_num3`。

**Live 驗證協議 (5554 2026-06-22, 回應長度都對上親手按鈕)**:
- 種植 = 3078 `{seed_id:103, land_id}` (build_plant_body)
- 施肥 = 3079 `{role_id:0, land_id, fertilizer_id:111, num:3}` (num 原 1=bug;reply new_land#3 state=2 即熟)
- 收成 = 3080 pick `{role_id:0, land_id}` (CMD_PICK;reply 3080 code#1;未熟 code=122)
- 收穫 = 3081 harvest `{land_id}` (build_harvest_body;reply 3081;空地/未熟逾時不回)
- 買卡 = 6914 `{shop_type:11, shop_id:1604, num}` (買 +1 verified)
- 打工 18177/18178 → reply 18184 (非自身 cmd)

**正確流程 (對齊使用者 + farm_v2/operations/harvest_card.py)**:
1. 取消打工 (18178→18184)
2. 清場(打工種的便宜作物不能吃卡的 30 株額度): 施肥(num3 催熟)→ 收成(3080) → 收穫(3081)
3. 買豐收卡 N 張 (N = HARVEST_CARD_BUY_COUNT=3 週上限)
4. 賺取迴圈 × (bought×5 輪, 30株/卡 ÷ 6株/輪): 種植103 → 施肥(num3) → 收成(3080) → 收穫(3081)
5. 恢復打工 (18177→18184)

**Decisions (使用者 2026-06-22)**: 直接跑 cards×5 輪固定,**不偵測 buff 耗盡**(清場後額度全新,基本固定)。land_ids 由開頭 read_farm 讀一次(fresh session 可讀;之後靠 fertilize reply 的 new_land state 判熟,不重讀 3077 因 ~once/session)。

**實作 todo**:
- [ ] `build_pick_body(land_id, role_id=0)` → `{role#1, land#2}`
- [ ] `pick_lands(client, land_ids)` — 送 3080,檢查 reply code#1,per-land 容錯
- [ ] `fertilize_until_mature(client, land_ids, num=3, max_rounds=8)` — 迴圈施肥,解析 3079 reply new_land#3 state==2 即停該塊;code19(已熟)也停;封頂避免肥料暴衝
- [ ] `harvest_lands(client, land_ids)` — 送 3081 per land,短 timeout + 容錯(空地逾時=跳過)
- [ ] worker stop/start expect 加 18184 → `(18184, cmd, 0x0201)`
- [ ] 重寫 `run_harvest_card_cycle`: stop_work → read_farm(land_ids + 有作物者) → 清場 → buy N → rounds=bought×5 迴圈 → start_work;回傳含 cleared/cards_bought/rounds/planted/harvested
- [ ] 更新/加測試 `tests/test_harvest_card_*`(mock client,驗 cmd 序 + 輪數 = bought×5)
- [ ] 不動 ADB 視覺版 farm_v2/operations/harvest_card.py(僅 WS 路徑)

### Review — 完成 2026-06-22
全部 todo 已實作 + 測試:
- ws_token/farm.py: `CMD_WORKER_STATE=18184` + worker stop/start expect 18184;`build_pick_body`;`HARVEST_CARD_WEEKLY_LIMIT=3`/`PLANTS_PER_CARD=30`;新 helper `plant_lands` / `pick_lands` / `harvest_lands`(含逾時重試)/ `fertilize_until_mature`(讀 3079 reply new_land state 判熟);`run_harvest_card_cycle` 重寫為 4 階段(取消打工→清場→買卡→賺取 bought×5 輪→恢復打工)。
- tests/test_ws_token_farm.py: +4 測試(fertilize_until_mature 迴圈到熟 num=3 / pick_lands 3080 role0 / pick code!=0 失敗 / harvest_lands 獎勵加總)→ **45 passed**。
- runner 相容:harvest_card + farm 的 11 個 runner 測試 passed(num_cards/fertilizer_id/inventory_tracker/device_id 簽名向後相容)。
- live 驗證:4 階段協議 + earn 37 增益 end-to-end 在 5554 跑通(tools/ws_harvest_step.py)。
- 既有失敗(非本次):runner 的 main_tasks 4 測試=08:00 時間閘(現 05:59);fake-transport 259 hang。8 點後/環境問題,與本變更無關。
- **待生效**:需重啟 new_main_v2(sys.modules cache);5554 等裝置要在 config 設 `ws_token.farm.harvest_card_cycle.enabled=true` 才會跑(目前 5554=null)。ADB 視覺版 farm_v2 未動。

## 修復:手機登入立刻被 WS 彈出(線上檢查連線打到真人帳號)— 2026-06-25

**問題**:真人在手機登入後立刻被 WS 彈出(異地登入)。
**根因**:線上偵測機制用「真人手機帳號」(roleId 89565100509472)連線:
1. `online_monitor` 預設 `preferred="fc65396d_u999"`(= 手機帳號)持久連線;真人登入踢掉它後,`_loop` 因 `_should_yield(手機)` 看不到 bot_state thread 而判定 idle → 立刻 reclaim 重連 → 又踢真人。死迴圈。
2. `online_check_checkers=['*']` 展開含手機(無 target_pid),一次性 WS 登入也可能用手機帳號。

**帳號別名陷阱**:`fc65396d_u999`/`adb-fc65396d-…_tcp` → roleId 89565100509472(手機);但 `fc65396d` → 89555436834913(其實是 5554)。保護必須以 **roleId** 為準,不能用裝置名字串。

**修復計畫(TDD)**:
- [x] 測試先行:`tests/test_online_monitor.py`(7)+ `tests/test_config_human_played.py`(2)→ 先 RED 後 GREEN
- [x] `bot_config.json`:手機裝置 `adb-fc65396d-…_tcp` 加 `"human_played": true`
- [x] `config_manager.py`:`get_online_check_checkers()` 的 `*` 展開排除 `human_played`;新增 `get_human_played_devices()`
- [x] `ws_token/online_monitor.py`:預設 `preferred="emulator-5554"`;`discover_role_map(protected_role_ids=…)` 過濾;`_connect` 以 roleId 拒絕保護帳號;`resolve_protected_role_ids()` 從 config 解析;`ensure_started`/CLI 預設改 5554
- [x] 新增:切換/重連 5 分鐘冷卻 `switch_cooldown_sec=300`(`_switch_allowed()` 閘住 failover 重連 + yield + reclaim),解「斷線後立刻重連又被彈出」
- [x] 驗證:focused pytest 9/9 綠;真實 config 驗證 phone 不在 checkers、protected={89565100509472}、preferred=emulator-5554

### Review — 完成 2026-06-25
**根因**:線上偵測(online_monitor 持久連線 + online_check_service 一次性登入)會用「真人手機帳號」連 WS → 異地登入踢真人;monitor `_loop` 又因看不到手機 bot_state thread 而判定 idle,斷線後立刻 reclaim 重連 → 死迴圈狂踢。

**修法**(2 層防護 + 冷卻):
1. 以 **roleId** 標記/排除真人帳號(裝置名有別名陷阱:`fc65396d_u999`/`adb-…_tcp`→手機 89565100509472;`fc65396d`→5554)。`human_played:true` → `resolve_protected_role_ids()` → monitor `_connect` 拒登 + `discover_role_map` 過濾;`*` checker 池排除。
2. preferred 改 `emulator-5554`(主路由)。
3. 切換/重連 5 分鐘冷卻,杜絕重連風暴。

**驗證**:`pytest tests/test_online_monitor.py tests/test_config_human_played.py -q` → 9 passed。online-check/config 回歸 64 passed。`test_online_check_immediate_wake` 3 紅 = 既有 stub 缺 `has_pending_web_close_request`(與本修復無關)。
**待生效/手動驗證**:重啟 `new_main_v2`(sys.modules cache);手動開手機帳號 H5,確認不再被彈出(5554 當主路由偵測)。
**未提交**:`bot_config.json`/`tasks/todo.md` 內含先前 session 的 WIP,為免混入無關變更,本次未自動 commit,改動已就緒待使用者決定。

### 追加:偵測器自動切換 + 儀表板顯示 — 2026-06-25
- 儀表板頂列新增「上線偵測」徽章(`/api/status` `online_monitor` → `routes_status._online_monitor_status` + `dashboard.html`),顯示目前負責偵測的裝置/新鮮度。
- 使用者回報「都在跑腳本卻沒自動切換、卡在閃電(=emulator-5554)」。根因:舊 `_loop` 啟動就硬連 preferred(不管它在跑腳本→同帳號互踢),且上一版把 5 分鐘冷卻也套到「讓位」→ 卡死。
- 重寫偵測器選擇(`_select_detector`/`_is_safe_detector`,移除 `_pick_idle_device`/`_should_yield`/`_try_connect`):**只連休眠中的裝置**當偵測器(在跑腳本的一律不選,避免同帳號互踢),preferred 在睡優先用、否則跳別台在睡的、全忙則暫不連線(走一次性備援)。讓位(忙→睡)立即切換;只有「連線失敗/被踢後重連」套 5 分鐘冷卻(防風暴)。
- 測試:`test_online_monitor.py` 加 `_select_detector` 3 案(preferred 在睡優先 / preferred 忙則讓位 / 全忙回 None)→ 10 passed。
- 待生效:重啟 `new_main_v2`。

### 追加 2:切換軌跡 + 刷新倒數 + 每卡在線標 — 2026-06-25
- 切換軌跡:`_set_active()` 記錄偵測器轉移(log `detector switch X->Y`)+ `last_switch`/`get_last_switch()`;徽章 tooltip 顯示「上次切換: X → Y」。測試 `test_last_switch_records_transition`(11 passed)。
- 刷新倒數(使用者選「下次刷新倒數」):`get_poll_sec()` + `/api/status` 回 `poll_sec`/`refresh_in_sec`;徽章改「上線偵測: 閃電（下次刷新 倒數 Ns）」,前端 1s ticker(`renderOnlineMonitor`)以 server 相對 `refresh_in_sec` 錨定本機時鐘倒數,避免時鐘偏差。
- 每卡「當前在線」(使用者選「保留 ONLINE 另加小標」):`/api/status` 每裝置加 `account_online`(由 `_account_presence()` 的 snapshot {role_id:online} + `_device_role_id` 解析,以 roleId 比對);卡片右上角保留 ONLINE,另加 `.acct-presence` 小標(在線=綠/離線=灰,unknown 不顯示)。偵測器本身不在自己好友列表→該卡無標(正常)。

## 跨服戰「放置獎勵」純-WS 自動領取（每8小時）— 計畫 2026-06-28

### 背景 / 已驗證協議（live CDP 9225, 5560, 伺服器 s1467）
跨服戰 = Activity type 33（biweekly，六10:00→日22:00）。「左下角寶箱」= 放置獎勵（掛機）入口。
模組 45 `cross_war`（cmd = 45*256 + sub）:
- `0x2d03` cross_war_idle_reward（查詢，空 body）→ reply `last_time`（上次領取 ts）+ report_list。
- `0x2d04` cross_war_get_idle_reward（**領取**，空 body）→ reply `new_last_time`。
- 累積量 = ratePerMin × floor(clamp(now-last_time, 0, 28800s) / 60)，**上限 8h，溢出丟棄**，rate 依戰力分級。輸掉 PvP 會把 last_time 往後推（吃掉累積）。
- **0x2d04 為伺服器權威、空 body、無前置 enter-scene**（領取鈕只送這一發；client 無任何 check；回覆僅帶新時間戳）。→ 純-WS 一發即領，不需進場景。
- live 實證:`call_raw(0x2d04, b"")` 成功領取 456000 金幣 + 9600 道具1181,計時歸零。
- 開放窗口由伺服器經 `act.act_list`(0x180c, 模組24) 下發 type33 `{state,start_time,end_time}`;biweekly 錨點非 client 硬編。

### 設計決策（lazy + robust）
- **不解析 act_list、不硬編 biweekly 錨點**:gate 只用「週末窗口(六10:00→日22:00) + 距上次嘗試≥8h」,送出 0x2d04 後**讓伺服器當權威**:非開放週末→伺服器拒(0x0201)或不回(timeout)→ benign skip。永不漏領(每個週末都試),off-week 僅多幾個無害 probe frame。
- 8h 間隔 = 對齊 8h 上限,符合使用者「每8小時」;領更勤無損失但多 chatter,故守 8h。
- dormant event 可能不回 frame → 短 probe timeout(6s)+ `WSTimeoutError`/0x0201 當 skip,不寫 marker。
- 無 Playwright 對應 → **不需** `WS_TO_PIPELINE_SKIPS` 條目。

### 實作清單（off by default;對 running bot 為新增 opt-in,不改既有行為）
- [ ] `ws_token/xwar_idle.py`(仿 `rogue.py`):`claim_idle(client)` → `call_for(0x2d04, b"", expect_cmds=(0x2d04, 0x0201))`;解析 reply 道具入帳(仿 secret_jewel `_parse_rewards`,讀 0x0406 推送或 reply)。
- [ ] `ws_token/runner.py`:`_run_xwar_idle(client, *, enabled, device, state_dir, now)` wrapper(仿 `_run_rogue`+`_run_dragon_realm`):週末窗口 + 8h 間隔(ws_state ledger `xwar_idle.{last_attempt_ts,last_success_ts,last_new_time}`,**attempt 一律寫 last_attempt_ts** 以節流 off-week)。import + 加 guarded `_step("xwar_idle", ...)` + `run_device(..., xwar_idle_enabled=False)` 簽章。
- [ ] thread flag:`game_actions/ws_phase.py:_run_device` kwargs + `runtime_services/ws_runner_service.py` extra_kwargs/run_device 呼叫。
- [ ] `config_manager.py`:`DEFAULT_DEVICE_CONFIG["ws_token"]["xwar_idle"]=False` + `_merge_ws_token_phase_config` 加 `_to_bool` 強制。
- [ ] `templates/dashboard.html`:`WS_EXTRA_FIELDS` 加 `{slot:'events', path:'ws_token.xwar_idle', type:'bool', label:'跨服戰每兩週自動領閒置獎勵（六10:00–日22:00,每8h上限）', def:false}`（零 bespoke JS）。
- [ ] `tests/test_xwar_idle.py`:gate 邏輯(窗口邊界 / 8h 間隔 / timeout & 0x0201 skip / marker 持久化),注入 now= + state_dir=tmp_path。
- [ ] `docs/protocol/CROSS_WAR_IDLE_REWARD.md`:記錄模組45 cmd 表 + 領取流程(目前無此文件)。
- [ ] live 驗證:用 5560 creds 起一條 fresh WSGameClient 送 0x2d04,確認 fresh 連線(非場景內)可領 → 證實「不需 enter」。

### FINALIZED gate（使用者選 act_list 權威判斷）— 2026-06-28
act_list 已 live 解出（0x180c 空 body,top repeated field 1 = 每個活動）:
type33 entry 欄位 `f2=type(33)`, `f5=state(2=Open)`, `f6=start_ts`, `f7=end_ts`, f8=phases, f9=reward cfg。
**開放判斷 = 送 0x180c → 找 f2==33 → open iff f5==2（亦可加 serverTime∈[f6,f7] 雙保險）。**

最終 gate（runner wrapper `_run_xwar_idle`,ledger `ws_state["xwar_idle"]={last_attempt_ts,last_success_ts,last_new_time}`）:
1. enabled? 否→skip。
2. now - last_attempt_ts ≥ 8h? 否→skip（純本地,節流所有 chatter 到 8h）。
3. set last_attempt_ts=now（持久化）。
4. 送 0x180c → type33 state==2? 否→skip（活動未開）。
5. 送 0x2d04 領取 → 成功則 last_success_ts/last_new_time 持久化 + log 入帳。
- 不需週末/biweekly 硬編（act_list 即權威,零漂移）。dormant/0x0201/timeout → benign skip 不寫 success。
- ponytail: 窗口末端 ≤8h 殘餘可能因 8h 節流落在關閉後而未領（每兩週至多漏一截）;升級路徑=end_ts 距今<8h 時放寬節流補領一次。先不做。

### 待使用者確認:實作隔離方式
目標檔有 3 個已有未提交 WIP:`game_actions/ws_phase.py`、`ws_token/runner.py`、`templates/dashboard.html`。
worktree-from-HEAD 會漏掉這些 WIP 且 merge 易衝突 → 建議直接在主工作樹改（變更為 off-by-default opt-in,不影響既有行為,bot 需重啟才載入）。待確認後動手 + 走 TDD（先寫 test）。

### Review — 跨服戰放置獎勵 純-WS 自動領取 完成 2026-06-28（branch feat/xwar-idle-reward）
協議 live 解出 + 接線完成（off by default opt-in；不動既有行為，bot 需重啟才載入）。
- [x] `ws_token/xwar_idle.py`：`parse_act_list`/`parse_claim`/`read_window`/`claim_idle`/`claim_if_due`（8h 節流 + act_list Open 閘 + ws_state ledger）。
- [x] `ws_token/runner.py`：import + `_run_xwar_idle` 薄包 + `run_device(xwar_idle_enabled=False)` + guarded `_step`。
- [x] caller wiring：`game_actions/ws_phase.py`（讀 cfg.ws_token.xwar_idle）+ `runtime_services/ws_runner_service.py`（啟用才入 extra_kwargs）。
- [x] `config_manager.py`：`ws_token.xwar_idle=False` 預設 + `_merge_ws_token_phase_config` `_to_bool` 強制。
- [x] `templates/dashboard.html`：活動頁新增 slot `xwar` + `WS_EXTRA_FIELDS` 一條（零 bespoke JS）。
- [x] `tests/test_xwar_idle.py`：12 案（parser + gate 邊界 + ledger 持久化）全綠。
- [x] `docs/protocol/CROSS_WAR_IDLE_REWARD.md`：協議文件。
- [x] 驗證：parser 對「真實 live bytes」（act_list 1572B + claim reply）解出 type33 Open / claim ok；回歸 test_ws_phase(49)/wiring+config(55) 全綠；config round-trip OK。
- [ ] 待使用者：fresh ws_token 登入 E2E（會踢掉 parked Playwright session）；或啟用開關 + 重啟 bot 自然驗證。merge 回 main + 刪 worktree。

---

## Plan: WS 挖礦 A(挖步導向) + B(決定論地形) — 2026-06-29（待使用者過目）

**動機（live 實證,5558+5554 CDP）**:WS `board_to_grid` 與 CNN 分類器（已確認為真相）逐格比對差 24/42。
根因有二,均純-WS 可解:
- A 挖步導向:executor fallback `_select_dig_step._key` 用「最深前緣、col 由小到大」,完全不看 `map_pits`。
  → 礦沒被優先收 → 漂到 r0 堆積/捲出視窗永久消失（5554 實測有 pit 卡在 r0 col2）。= 「繞著礦坑挖」。
- B 地形半盲:`0x0c01` 不送未挖格地形;前端用 `configMine_template` 客戶端自鋪。現 `mine_terrain` 邊挖邊學(慢、半盲)。

### B 規則已 live 解出並驗證(2 帳號 54/54 未挖格 0 誤)
```
q=depth+6 ; area=q//7 ; tpl_row=q%7

---

## Plan: 挖礦 多步道具組合規劃（先A再B再C）— 2026-07-05

**目標（使用者兩次糾正後收斂）**：純 WS 挖礦路徑；道具是庫存不是無限（不降價狂用）；
同樣道具支出下用「多步組合」最大化每顆道具效益。

- [x] 1. 研究：v1/v3/v4 道具決策機制 + 執行面 — 結論：live WS 的道具決策大多走
      `_select_dig_step` 的 `prop_step_for_pit`（單發貪婪、炸彈無條件優先=bug），繞過 plan_smart lookahead
- [x] 2. 研究：真實 log — bomb 庫存數百(淨累積)、drill 數十(真稀缺)、2+道具 plan 僅 0.23%
- [x] 3. Baseline：sim v1 948/186（同 skill 記錄）；32 真實 board 全 <300ms
- [x] 4. Phase A 掃描：道具降價=省鎬不加分(AB40 246→231)、v4 加深無效(925→920)、
      v1adb(967)>v1(948) → 短路保留；結論=量不是槓桿、「用得準」才是
- [x] 5. 實作：`prop_combo_for_pits` 有界 DFS 道具序列(≤3、每顆自身≥2 pit 門檻、
      庫存/allow 尊重、修炸彈優先偏置、tie-break 省 drill)，`prop_step_for_pit` API 不變
      回傳組合首步（commits 02d40536 test + 94c3786d feat；implementer 誤 commit 到 main，就地審查通過）
- [x] 6. 評估：eval_prop_combo 3 seeds x2000 盤 → 每盤收礦 +3.7~4.0%、組合率 61→68%、
      決策改變率 ~44%、avg ~1ms；99 個相關測試全綠；sim(948/186)/真實board(0 empty, max 185ms) 回歸無漂移
- [x] 7. 報告 — 見下方 Review

### Review — 挖礦多步道具組合（純 WS）完成 2026-07-05
- 交付：`ws_token/mining_adapter.py` `prop_combo_for_pits` + `tools/eval_prop_combo.py`（MC A/B）
  + `tests/test_ws_prop_combo.py`（10 案）。順手修既有測試污染（test_mining_item_logic 收集期
  stub smart_planner → 同批 WS 測試全空 plan）commit cbbf63fb。
- 效益：同一 ≥2 pit 價值門檻下，組合搜尋每盤多收 ~3.8% 礦；修掉炸彈優先偏置（drill 命中多時改選 drill）。
- 未做（政策留給使用者）：道具重定價（sim 顯示可省鎬 60%+ 但道具=庫存不重定價）；
  v1 的 drill→bomb 配比調整（b2.0/d4.0：分數持平、drill 消耗 -63%、bomb +11/局，數據在此不動手）。
- 可選後續：live WS session A/B（fresh 登入會踢 parked Playwright，擇機）；更保守可調 min_pits=3。
terrain[depth][col] = configMine_template[ area_info[area] ][tpl_row][col]   # 100 air / 201 dirt / 202 stone
```
- `area_info` 的 value 就是 template id（`board.area_info` 已解析,在 0x0c01 裡）。

---

## Plan: 在線檢測延遲 + detector 自身不顯示在線 — 2026-07-05（待使用者過目）

### 使用者回報（三症狀）
1. 在線檢測延遲，超過 30s poll 能解釋的範圍。
2. 介面快照顯示「剛剛」（很新鮮），但目標實際已下線仍寫「在線」。
3. 被選為監視者（detector）的裝置，介面上自己不顯示在線。

### 根因（已查證，非猜測）
- 好友列表 sf7 `last_login_ts` 語意（`web_game_api.is_player_online` 實測註記）：
  `0` = 現在在線（sentinel，實測可靠）；`>0` = 離線時間戳（= 遊戲內「X 分鐘前」）。
- `poll_friends`（`ws_token/online_monitor.py:89-104`）把 `online=(now-ts)<threshold_sec`
  **烘進快照**，monitor 預設 `threshold_sec=120` → 登出後最多 120s 仍被判「在線」，
  加上 30s poll 週期，最壞 ~2.5 分鐘才轉離線。快照 age（剛剛）與 entry.online
  是兩回事 → 症狀 1+2。
- `_check_monitor_snapshot`（`runtime_services/online_check_service.py:83-113`）fast path
  直接回烘入的 bool（120s 門檻），**忽略 requester 配置的 `online_check_threshold_sec`(60)**。
- detector 永遠不在自己好友列表 → `_account_presence`（`control_panel/routes_status.py:434-441`）
  的 {rid: online} 沒有它 → 徽章不顯示在線 → 症狀 3。monitor 明明持有它的活 WS session。

### 修法（最小 diff；poll_sec=30 不動）
- [x] A. `routes_status._account_presence`：顯示層改用 `last_login_ts == 0` 精確判定
      （StatusEntry 本來就帶 ts），並 overlay `current_detector()` 的 rid → True。
      效果：登出後最慢下一輪 poll（≤30s）徽章就轉離線；detector 顯示在線。
- [x] B. `online_check_service._check_monitor_snapshot`：改收 threshold 參數，用
      entry.last_login_ts + requester 的 threshold 重算（ts None → None → 落一次性 WS 路徑）。
      效果：5558 互檢延遲從 ~150s 降回 threshold(60)+poll(≤30)。
- [x] C. 不動：guard 路徑（`account_online` / ws_phase 人帳保護閘）維持烘入 120s 寬限，
      登出後多等 2 分鐘才敢動人帳是保護特性，不是 bug。
- [x] 測試：新增 `tests/test_online_presence_display.py`（8 案，TDD 先紅後綠）；
      相關既有測試 52 個全綠（online_check_service / ws_human_offline_gate /
      config_role_id_cache / online_monitor）。

### Review — 完成 2026-07-05，commit b5e26567（worktree 開發後 ff-merge 回 main）
- 生效需重啟 `new_main_v2.py`（無 hot-reload）。
- 已知顯示差異（設計如此）：徽章比互檢/guard 更快轉離線（徽章精確、閘門保守）。
- detector overlay 只在 monitor 連線中成立；monitor 斷線時該裝置徽章回歸快照原值。
- area_info 帶 prev/cur/next 3 個 area = 21 列,覆蓋 7 列視窗(跨 ≤2 area)綽綽有餘。
- 模板來源:`docs/protocol/mine_config_tables.json`(已有,= live configMine_template)。

### 實作項目(TDD,worktree branch `feat/ws-mining-terrain-steering`)
- [ ] B1 `ws_token/mine_terrain.py`:新增 `terrain_at_static(depth,col,area_info)` 走上式直接查表(免學習)。
  保留舊學習路徑當 area_info 缺失時 fallback。
- [ ] B2 `ws_token/mining_adapter._project_board`:off-frontier 未挖格改用 static 查表(精確 dirt/rock/air),
  取代現「預設 dirt + 學習模型 override」。WS 真相(dug/pit/active)仍最優先。
- [ ] B3 test:用 5558/5554 兩盤 fixture,重建地形 == 擷取到的 BlockRoot/CNN(逐格)。
- [ ] A1 `ws_token/mining_supervised._select_dig_step`:fallback 改 pit-directed —
  在可挖 frontier 中選「離最近未採集礦坑(含 `map_pits` 下方)直線距離最小」者,優先沿礦欄下挖;tiebreak 最深。
- [ ] A2 收礦優先:可達 pit(在 actives)未收前不捲動越過(強化既有 hold_floor,涵蓋「漂到 r0」)。
- [ ] A3 test:合成盤面 pit 欄 ≠ 最深前緣欄 → 斷言選 pit-directed 格,而非最深-最小-col。
- [ ] 驗證:對 5554/5558 擷取盤面 replay,確認導向朝礦 + 地形 == CNN;py_compile + 相關 pytest。
- [ ] off-by-default? 否 — 此為修正既有 WS 挖礦行為(非新功能),預設生效;bot 重啟才載入。merge 回 main + 刪 worktree。

## Plan: 移除工具頁「看廣告獎勵」獨立面板 — 2026-07-04（已完成 98910dd1）

**動機**: 看廣告獎勵已整合進每日純 WS 腳本（`ws_token/runner.py` `_step("ad_rewards")`，
由 dashboard「任務開關 → 看廣告獎勵」`ws_token.ad_rewards.enabled` 控制），
tools_optimize 頁的手動面板（讀取當日進度 / 一鍵領取）冗餘。

**保留（每日腳本依賴，不動）**:
- `ws_token/ad_reward.py` + `tests/test_ws_token_ad_reward.py`
- `ws_token/runner.py` ad_rewards step、`game_actions/ws_phase.py` `_ad_reward_ids`
- `templates/dashboard.html` 的 ws_token 設定開關（chkAdEnabled / chkAdSeed / config_ids UI）
- `tests/test_config_ad_rewards_default.py`

**移除項目**:
- [x] `templates/tools_optimize.html`: 看廣告獎勵 section（HTML + JS：status/claim fetch、adMsg 等）
- [x] `control_panel/routes_ad_reward.py`: 整檔刪除（blueprint 僅供該面板）
- [x] `control_panel_app.py`: 移除 routes_ad_reward import + 註冊（2 處）
- [x] `tests/test_ad_reward_routes.py`: 整檔刪除
- [x] `tests/test_tools_optimize_template.py`: 刪 `test_ad_reward_section_hooks`
- [x] 驗證: py_compile + `python -m pytest tests/test_tools_optimize_template.py tests/test_ws_token_ad_reward.py -q`

**注意**: bot_config.json 有 2 台裝置 `ad_rewards.enabled=false`（其餘皆 true）。
面板移除後這 2 台沒有手動領取入口，要領就到 dashboard 開啟該裝置的看廣告獎勵開關。

## ✅ 2026-07-06 科技園研究加速廣告接入純 WS（完成，待 Probe B 補驗）

**協議事實（client 原始碼 + live 驗證）**:
- 科技園「跳過30分鐘」= AdType **5 = AD_SCIENCE_1**（非建築加速 17），每日 4 次，走統一廣告
  通道 `ad.ad_reward_c2s` 0x1602。
- `science.science_info_c2s/s2c`（module 11，cmd **2817**，c2s 空 body）= 唯一讀「研究中？」的來源：
  s2c repeated field#1 `ScienceTreeInfo{type#1, doing#2(science_id,0=idle), etime#3, science_list#4}`。

**Probe A（2026-07-06，小寶 7fe98fc6，CDP 9226，`tools/probe_science_ad.py claim`，live 驗證 OK）**:
科技樹 doing=1023（研究中）時 claim `ad_reward(5,is_free=1)` → `count` 0→1、`etime` 減少 1832s
（≈30min+來回延遲），確認協議假設成立。

**Probe B（未測，doing 全程非 0，今日只留 1 次額度沒有刻意再燒）**：閒置時 claim 會 reject 還是
白燒次數仍未知 → 用防禦性做法解決，不用被動等這個狀態出現：`ws_token/ad_reward.py`
`is_science_researching(client)` 先讀 `science_info`，`claim_ads` 對 config_id 5 在
`doing==0`（或讀失敗，fail-closed）時直接 skip，不送 0x1602。

**接線（commit 待做）**：
- [x] `ws_token/ad_reward.py`：`CMD_SCIENCE_INFO=2817`、`AD_SCIENCE_1=5`、`TIMES[5]=4`、
      `AD_NAMES[5]="科技研究加速廣告"`、`is_science_researching()`、`claim_ads` 對 5 加 doing gate。
- [x] `tools/probe_science_ad.py`：CDP-attach 探針（不開新 WS 登入，不踢真人），`state`/`claim` 子命令。
- [x] `tests/test_ws_token_ad_reward.py` +7（doing gate true/false/read-fail-safe、claim_ads skip/claim）；
      全檔 28 綠。
- [x] **全裝置啟用**（2026-07-06，commit cf7e3252）：`bot_config.json` 8 台 `ad_rewards.config_ids`
      都加 `5`（未動 `DEFAULT_CONFIG_IDS`，仍是各裝置 opt-in 清單驅動）。
- [x] **GUI 冗餘跳過**：`ws_token/runner.py` `_run_ad_rewards` 在 WS 實際加速成功（claimed>0）或
      額度已滿（"maxed"）時，寫 `json_manager.time_recording(ip, "daily_acceleration")`——
      跟 `game_actions/task_due.py:_due_daily_acceleration` 讀的是同一筆記錄，讓
      `daily_tasks.daily_acceleration`（進科技園點 5 次「跳過30分鐘」的 ADB/H5 流程）自動 skip，
      不用再跑一趟。「無研究中」skip 不標記（沒做到事，留給 GUI 自己判斷）。
- [ ] ⚠ 需重啟 `new_main_v2.py` 生效（runtime 模組，無 hot-reload）。

---

## Plan: 賞金之路 (Escort) 自動打 NPC — H5 接入每日流程（2026-07-04，待使用者過目）

### 目標
web_h5 裝置在賞金之路開放時，自動打地圖上的 monster NPC（虛偽騎士 / 巫師娃娃 / 海灘奸商），
清一輪即止。打輸的 NPC 跳過續打下一隻。整合進 `daily_pipeline` 尾段，dashboard 有開關。

### 已 live 驗證的事實（5556 / CDP 9223，見 memory `reference_escort_bounty_road`）
- 賞金之路內部名 = Escort；NPC 挑戰 = client 端 `護送戰鬥`（battleMain 確定性 + anti-cheat）→ **只能 H5，不可純 WS**。
- 排程本質同**跨服戰**（雙周末、與跨服戰交錯、平常關閉）→ 開放偵測 live 判定、不硬編錨點。
- UI 流程（全 `/UIRoot/NormalView/`）：
  - 首頁 banner `MainView/top/systemTop/btnRoot/btnEscort`（賞金之路，活動關閉時不存在）。
  - `EscortView/.../EscortMainView/EscortMainView/nodeBattle/escortBtn`：`mask.active===false` = 開放中。
  - 地圖 `EscortTransportSceneView/ScrollView/view/content/player` → name==`monster` 子節點 = NPC。
  - 點 monster → `EscortMonsterFightView`（`btnStart` label=挑戰）→ 點 btnStart → 戰鬥（`BattleView/BattleHubView`, AUTO, ~15-25s）→ `EscortResultView`（`nodeWin`/`nodeDefeat`）→ 點 `imgMask` 關閉回地圖。
  - 打贏 NPC 節點變 `active:false`（留原地變灰）；打輸行為未直接觀測 → loop 用「每隻只嘗試一次」避免無限重打。

### 設計（範本：gating 學跨服戰、driver 機制學 fannaoxiao）
- **日/時閘門（便宜前置，使用者指定 2026-07-04）**：只在**週六/週日**且本地時間 **>= 11:00** 才動作；
  否則直接 return（不讀 page、不導航）。週一~五完全 no-op（「無須每日尋找」）。
- 開放檢查（兜底 biweekly 交錯）：過了日/時閘門後，live page 讀首頁 `btnEscort` banner active/存在
  → 不存在即該週末非賞金之路（可能是跨服戰週末），**不導航直接 skip**。
- daily 冷卻（~20h）：六日每天最多清一輪，避免 11:00 後每次喚醒重入；成功才記錄，失敗下輪重試。
- loop 護欄：進地圖前收集所有 monster 節點 `uuid`；逐一嘗試（still active 才打），打完/打輸都標記 attempted → 一輪最多打每隻一次，絕不無限。

### Global Constraints（本 repo）
不加新套件；JSON 讀 `utf-8-sig`；pytest 必指定檔；只 stage 動到的檔（不 `git add -A`）；不 push、不加 footer；worktree 隔離。

### Tasks
1. **escort_driver.py**（新檔，`game_actions/`）：`enter_escort(page)`（banner 在→導航→確認 open，回 bool）+ `fight_npc_round(page, max_fights=8, ...)`（枚舉 active monster → per NPC: 開 fight view → btnStart → poll EscortResultView → 讀 win/lose → 點 imgMask 關 → 標 attempted），回 summary dict（fought/win/lose/skipped）。cocos JS 內嵌，emit('click')。
2. **escort_scheduler.py**（新檔）：`run_escort_if_due(d, ip, driver=None)` 頂層守衛（flag? web_h5? due? live page?）→ pause_guard bind → enter+fight → 成功記錄。全包 try/except。照 fannaoxiao_scheduler 結構。
3. **config_manager.py**：`DEFAULT_DEVICE_CONFIG` 加 `enable_escort: False` + dataclass field + set_device_config 轉型清單。
4. **daily_pipeline.py**：import + 在 fannaoxiao 呼叫（~L427）附近加 `run_escort_if_due(d, ip)`。
5. **templates/dashboard.html**：加 `chkEscort` checkbox（~L4321 讀 config.enable_escort / ~L4477 寫入 payload）+ 對應 label。
6. **tests/test_escort_task.py**：閘邏輯測試（flag off / adb backend / 已跑過 / due+web_h5 跑一輪+記錄 / 無 page / 0 fights 不記錄），FakeDriver 注入，照 test_fannaoxiao_task.py。

### Live 驗證（build 時，5556 web_h5）
- [ ] 從首頁走完整 enter_escort（我先前是活動已開才進，需驗證首頁→地圖點擊序列）。
- [ ] fight_npc_round 實跑一輪：多隻 NPC 連打、關閉、attempted 去重、summary 正確。
- [ ] 關閉時活動 banner 不在 → enter_escort 正確回 False skip。

### 不做（YAGNI）
- 大盜來襲（bossBtn）— 之後另開。
- 打真人 playerItem（耗 3/3 次數）。
- refreshBtn 重刷、付費刷新。
- ADB 後端（H5 驗證後另開）。
- 打輸重試、戰力不足自動停整台（本輪用「跳過該 NPC」已足）。

---

## 全面體檢報告 — 2026-07-05（8 Opus agents：4 log + 4 code audit）— ✅ 2026-07-05 已全數修復 merge 回 main

> 只分析未修改。log 涵蓋 07-02~07-05 全部裝置；code audit 涵蓋 miner/ws_token、game_actions/farm_v2/opengold_v2/task_sandbox、utils/核心入口、control_panel/runtime_services。
> 所有 dead-code 皆經 repo-wide grep 驗證（含 tools/tests/templates/static）。

### 挖礦結論：健康
- WS 挖礦：5 裝置全部正常以 `pickaxe_empty` 收尾；hold_floor 僅 2 次且 1 輪自復原；鎬/鑽/炸彈逐場對帳一致，無 desync、無 rate-limit、無空 plan 卡死。
- ADB 挖礦（5558）：零例外零崩潰；session 均正常收尾。僅低嚴重度兩項（見 M9/M10）。
- 記憶中「bomb OCR 讀 0」bug 確認只在 web_h5 路徑（web-002 有實錘），ADB 路徑讀值正常。

### A. Runtime 維運（非改碼即可處理）
- [x] **A1 [HIGH] emulator-5556 執行緒卡死**：07-04 21:05 `app_start reused game url loaded during restart`（device_wrapper.py:986 重啟路徑）後靜默 6h+，game-init 未觸發，無 watchdog 救援。→ 先查活體/重啟；考慮加 per-thread stall watchdog。
- [ ] **A2 [HIGH] emulator-5558 缺 WS 憑證**：每輪 `no captured creds` 退回全 Playwright，連鎖萬神試煉失敗+10 輪「不在主頁面」。→ 跑 `python tools/adb_token_login.py --device emulator-5558`。
- [ ] **A3 [INFO] 賞金之路生產未驗證**：合併後因雙週 gating 尚未執行過，建議首個開放週末盯 log。
- [ ] **A4 [INFO] online_monitor FORCE blind-connect 6 次**：guard 正確（全部排除 human 帳號 5558），但 snapshot stale >300s 頻率偏高，偵測覆蓋可調。非急。

### B. 真 bug（行為錯誤，建議修）
- [x] **B1 [HIGH] new_main_v2.py:386 病句**：`== "滑動解除節電模式'"` 字串尾多一個 `'`，比對永遠 false → 節電滑動解鎖畫面時 `unlock_screen()` 永不執行。修：刪多餘引號。
- [x] **B2 [MED] ws_token/lamp.py:344（另 472/523/578/583）**：WS 神燈 EQUIP 後 `wear`（cmd=1282）31/31 次 no-response timeout；`sell`（cmd=1285）同。裝可能根本沒穿上，或伺服器沉默成功導致誤報+`equipped=N` 統計失真。修：穿後補讀裝備狀態驗證，或把 no-reply 視為成功。
- [x] **B3 [MED] utils/logging_utils.py:176**：`_DEVICE_LOGGER_NAME_PREFIXES` 漏 `ws_ad_reward_`/`ws_lamp_` → 這兩類 log 靜默不橋接到 dashboard ring buffer。修：補進 tuple。
- [x] **B4 [MED] utils/model_sync.py:71-92**：chunked copy 無 `copied == remote_size` 驗證即 atomic rename → NAS 不穩時可能把截斷模型提升為 local 並載入。修：rename 前驗 size/hash，不符 fallback remote_path。
- [x] **B5 [MED] utils/pause_guard.py:116**：註解說「快照失敗(空)→視為 diverged abort」，但兩次快照都失敗時 `"" == ""` 不會 abort → 在未知頁面狀態下續點。修：`if before != after or not before or not after: raise`。
- [x] **B6 [MED] game_actions/daily_tasks.py:50**：每日加速「無法進入家園頁面」失敗後不回主頁 → 同輪後續 4+ 任務連環「不在主頁面」、pipeline 強制中止（5554×2、5556×1 實錄）。修：失敗路徑先導航回主頁再 return。
- [x] **B7 [MED] game_actions/weekly_trials.py:134/198**：萬神試煉結算開不出「結束本局」→ 中止後停在非主頁，連鎖污染後續任務（5558 rotated log 23 次）。修：結算失敗強制回主頁。
- [x] **B8 [MED] control_panel/routes_status.py:449**：`_device_role_id` 用 `lru_cache` 無失效機制 → runtime 改 config 後 `account_online` 徽章持續顯示舊 role_id。修：移除 cache 或 config 寫入時 clear。
- [x] **B9 [LOW] miner/planning/item_planner.py:15**：`TOOL_DEBUG=True` 留在 v1 熱路徑（每輪 planning 多發 print 直上 stdout，繞過 per-device logger）。修：預設 False。
- [x] **B10 [LOW] control_panel/routes_status.py:53-58**：OCR `server_mode: "auto"` 與 `"main"` 分支逐字相同，「auto」零效果。修：合併分支或實作真正的 auto 排序。
- [x] **B11 [LOW] utils/screenshot_helpers.py:38**：`log_main_page_mismatch` 呼叫 `capture` 未包 try/except（imwrite 失敗會 raise 出防禦性 helper；同檔 `save_error_screenshot` 有包，不一致）。
- [x] **B12 [LOW] utils/ws_listener.py:424-431**：drain 高水位在查詢前取樣 → 視窗內 frame 可能跨兩次 drain 重複回傳（telemetry 重複，不致損毀）。

### M. 挖礦低優先改進（log 實證，非急）
- [x] **M9 [LOW] miner v4 no_pit 有時回空步**：盤面仍有可達 dirt 卻連 3 空 plan 提前中止（5558×2）。與「v4 應持續吐進度挖步」設計不符。查 miner/v4 no_pit 分支。
- [x] **M10 [LOW] 鏟子漂移 +3/+4 反覆 WARN（68 次，66 正）**：executor 未把「挖到 pit 反獎鏟子」計回內部 counter，OCR 校驗每次上修。功能無害，telemetry 不準。mining_service.py:549-569。
- [x] **M11 [INFO] ws_mining log 每步印 `hold_floor=False`**：干擾關鍵字告警；監控應改盯 summary 的 `hold_floor_rounds=[1-9]`。

### D. 死碼（全部 grep 驗證零呼叫；確認後可刪）
- [x] **D1** device_wrapper.py:251-357 `PlaywrightContextAdapter`/`PlaywrightContextConfig`/`DEFAULT_PLAYWRIGHT_CONTEXT_OPTIONS`（~100 行廢棄抽象層，從未實例化）
- [x] **D2** game_initialization.py `check_on_line` + `_check_on_line_via_protocol` + `_check_on_line_via_ocr_legacy`（舊 5558 線上檢查，已被 online_check_service 取代；new_main_v2.py:35 / wake_up_handler.py:9 的 import 也是死的；test mocks 過期）
- [x] **D3** game_initialization.py:437 `check_on_line_protocol_only` + `CHECK_TARGET_NOT_FRIEND`（從未接線）
- [x] **D4** new_main_v2.py:600 `temporary_reset_cycles`（一次性手動工具留在入口）；new_main_v2.py:8 重複 `import os`
- [x] **D5** img_tools.py:224 `save_stage_debug_image`（且 imwrite 被註解掉，宣稱有存實際沒存）；:41/:50 `set/get_ocr_server_mode`；:429/438 `find_and_click` 死目錄建立
- [x] **D6** utils/carpark_auto.py:1001 `park_one_normal`、:880 `recall_n_cross`、:759 `recall_one_cross`
- [x] **D7** utils/wake_up_handler.py:261 `_is_5554_busy_by_state`、:179/:183 `get_lock_status`/`set_lock_status`、:256 無效 `global _wakeup_lock`
- [x] **D8** utils/web_game_api.py:415/470/495 `parse_lamp_drops` 三件組（equipment_cache 已另行正確實作）；:712/756 `is_login_conflict`/`kick_reason`
- [x] **D9** game_actions/periodic_tasks.py:13 `should_execute_sea`（live 走 json_manager 版）
- [x] **D10** game_actions/daily_tasks.py:127 `claim_daily_free_pack`（實際用的是 daily_gift_task 的同名異函式）
- [x] **D11** farm_v2/operations/weekly_card.py 舊流程 `run_weekly_card`/`buy_shop_items`/`collect_weekly_card`/`do_fertilize`/`cancel_work`（保留 `check_if_parttime`）；plant.py `plant_one`/`plant_cycle`（保留 `check_slot_color`）；states.py 廢棄 state-machine + manager.py:14 未用 import
- [x] **D12** opengold_v2/config.sync-conflict-20260411-*.py（Syncthing 衝突孤兒檔，直接刪）
- [x] **D13** miner/planning/planner.py:257 `plan_greedy_with_rewards`、:47 `is_void`；mining_service.py:722 `demo_plan_print` + 其唯一依賴 `plan_min_cost_to_floor7`
- [x] **D14** control_panel/routes_worker.py:43-46 `global_resp` 死賦值；routes_status.py:182 `/api/device_data/<ip>` 無前端呼叫；shared/command_queue.py:112 本地 `recover` 分支不可達

### S. 文案/註解過期（低優先，順手修）
- [x] **S1（重要）** ws_token/mining_adapter.py:1-9 docstring 說 v5/plan_v4，實際 :514 呼叫 **v1 `plan_smart`** → **CLAUDE.md「WS 挖礦走 v4」與 memory 已過時，需一併更正**（log 中 "A* Planning" 訊息即為 v1 實證）。
- [x] **S2** miner/v4/planner.py:1 說 5-step 實際 MAX_DEPTH=3；:266 說 drill cost 1.5 實際 2.5
- [x] **S3** game_actions/dungeon_scheduler.py:6 docstring 週二~五，實際 gate 到週六（task_due 正確）
- [x] **S4** task_sandbox/tasks/lamp.py:38 references 仍列廢棄 V1 `Open_gold_paddle_ocr.py`

### P. 待使用者決策
- [x] **P1** labeler/trainer/`/api/ocr_config` 等全域操作端點只擋登入、無 `require_admin`——非管理員可觸發訓練/改全域 OCR 設定。要不要收緊？
- [x] **P2** D 區死碼是否整批刪除（建議一個 chore branch 一次清）？

### Review — 全面體檢修復完成（2026-07-05，5 修復 agents worktree 隔離 + review agent 守門後 merge）
- 5 分支全數 merge（bb541a4a 為最終），淨刪 ~1,000 行死碼；合併後 sanity 14 檔 **198 tests 全綠**。
- **B2 真相反轉（live CDP 實證）**：wear 成功回 0x0504 equip_change_s2c、被拒回 0x0201(code@f1)、無 1282 echo——31 次「失敗」其實全部穿裝成功，只是等錯 cmd 白等 8s。lamp.py 已改 expect CMD_EQUIP_CHANGE；sell 未實測（不可測試性販售），timeout 視為推定成功。探測腳本 tools/probe_wear_reply*.py 可複用。
- **D2/D3 兩度反轉**：舊基底 agent 誤判「還有呼叫端」，rebase 後確認呼叫端已被 online_check_service 取代 → 全刪（game_initialization 681→304 行）。
- **S1 連鎖更正**：WS 挖礦實際走 v1 plan_smart（sim 3711 vs 1649），CLAUDE.md「走 v4」為過期敘述——文檔清理 agent 更正中；memory 已更正。
- A1（5556 卡死）已由使用者手動開瀏覽器解除，per-thread watchdog 未做（另議）。
- 未竟事項：A2 需使用者決定是否給 5558 抓 WS 憑證（human 帳號，見下方注意）；M11 為監控建議無程式碼變更；farm_v2 config 殘餘座標與 CLAUDE.md 更正由文檔 agent 處理。
- ⚠ 全部變更需重啟 new_main_v2.py 才生效（無 hot-reload）。

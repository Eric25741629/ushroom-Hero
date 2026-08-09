# tasks/todo.md（2026-06-20 壓縮）

## 2026-08-09 狀態機 vs 任務註冊表：三份意見書統整與裁決

來源：`程式碼重購.md`（下稱 **A**）、`狀態機與註冊表說明意見.md`（**B**）、
`.worktrees/state-machine-registry-v2-gpt/狀態機與註冊表說明意見v2-gpt.md`（**C**）。
所有數字以 2026-08-09 `main` 實測為準（本節每條主張都經過 grep/AST 驗證）。

### 裁決總結（一句話）

三份都對「不要把整個系統改寫成巨型狀態機」、「該做任務註冊表」有共識；
真正的分歧只有一條：**要不要為裝置生命週期做一個窄狀態機**。
B 說不要（列觀察清單），C 說要。**這點 C 對，但要排在註冊表之後**，
而且必須受 A §4「唯一消費者必須是 live 路徑」這條規則約束。

C 貢獻了 A 與 B 都漏掉的最有價值設計：`TaskResult` / `TaskOutcome` 標準化，
以及把 `INTERRUPTED`（使用者強制休眠）與任務失敗分開。這是註冊表能不能用
通用執行器跑起來的前提，應納入最終規格。

### 誰說對了（實測核對）

| 主張 | 出處 | 實測 | 判定 |
|---|---|---|---|
| `daily_pipeline.py` 557 行、`_run_tasks` 388 行 | A | 556 行；`_run_tasks` :170-556 ≈ 387 行 | **對** |
| 每個任務重抄 6 個關切 | A | `_force_sleep_checkpoint`×28、`_ws_skip`×16、`_guarded_run`×13、`update_state`×8、`time_recording`×4、`is_due`×2 | **對** |
| 任務編號破表到 14.65、docstring 仍寫 20 | A | 註解編號確實到 `14.65`；`:171` docstring 寫 "20 tasks"，實際 28 個 distinct task（Task 5 & 6 共用一行註解） | **對** |
| `TASK_ORDER` 是已驗證可行的正確形狀 | A | `runner.py:90`，40 項 tuple + registry 分派 | **對** |
| `task_due` 只收斂了「due」一個維度 | A | pipeline 內 `is_due` 僅 2 處，其餘 5 維仍內聯 | **對** |
| `ws_done` 是同一份清單維護兩次的症狀 | A | `daily_pipeline.py:119` + `ws_phase.py:28/56` 兩張 dict，靠字串比對 | **對** |
| 三後端平行實作是體積的真正乘數（最大槓桿） | A §5.1 | `_sea_dispatch` :76-85 即縮影；B §7、C §5.2 亦同意 | **對，且為最大槓桿** |
| 隱性契約清單（Task 4 stage 給 5/6、5558 跳守護靈等） | A §2 | `:71` `_DEVICE_SKIP_GUARDIAN`、`:300/:311`、`:253/:547` 全部確認 | **對，搬動時必守** |
| 平行表格散在 5-7 處，新增任務要改 7 個地方 | B §2.1 | `TASK_ORDER` + 2 張 dict + 特例 schema + config flag + 8 個 scheduler，確認 | **對** |
| `game_actions/` 下 8 個 scheduler 是複製貼上模式 | B | 8 個檔案確認存在 | **對** |
| repo 衛生：stale worktree 數十個 | B §5 | `.worktrees/` 26 + `worktree/` 8 + `.claude/worktrees/` 46 = 80 | **對** |
| `main()` 同時負責過多狀態轉移，新增中斷條件會波及多個出入口 | C §2.1 | `main()` :146-782 = 637 行、88 個控制分支 | **對（C 自己還低估了）** |
| 「有狀態資料 ≠ 有狀態機」：缺合法轉移／優先級／消費語意定義 | C §2.3 | `bot_state` 有 `Signal`/status，但無集中 dispatch | **對，且據此駁倒 B §3.2** |
| `INTERRUPTED` 必須與失敗分開 | C §5.3 | A、B 皆未提及 | **對，且獨到** |
| 轉移函式不得執行重型 I/O、狀態數預算 8-10 | C §8 | — | **對，護欄合理** |

### 誰說錯了

| 錯誤主張 | 出處 | 實測 |
|---|---|---|
| `utils/page_detector.py` 是**未接線**的死抽象；「四個抽象層，四個沒進 live path」 | **A §4** | **錯。** `page_detector` 已在 live 路徑：`game_actions/stage_guard.py:45` 匯入 `try_detect_main_page_fast`，由 `get_stage_with_check()` 每次取 stage 都呼叫（per-device `experimental_cocos_navigation` 旗標控制）。應為「四個之中三個未接線」 |
| `TASK_ORDER` 有 41 個任務 | B §2.1 | **錯。** 40 個（A 說 40 才對） |
| `runner.py` 2130 行、`tests/test_ws_token_runner.py` 1729 行 | B §1/§2.2 | **錯（已漂移）。** 實測 2384 行 / 2275 行。B 的「每月 +500 行且無收斂」趨勢論**反而更成立** |
| `ws_state/` 不該進 git（暗示尚未處理） | B §5 | **已完成。** `.gitignore:147` 已列 `ws_state/`，git 追蹤數 0 |
| sync-conflict 殘骸污染 git | B §5 | **部分錯。** git 追蹤的 sync-conflict 檔案數為 **0**；但 `ws_token/lamp.sync-conflict-*.py`、`online_monitor`、`mining_adapter`、`game_actions/equipment_scheme` 等**確實存在於工作目錄**，污染 grep/find 導航（B 的結論對，理由錯：問題在檔案系統噪音，不在版控） |
| config 旗標 36 個 | B §2.1 | **不精確（低估）。** `DEFAULT_DEVICE_CONFIG` 65 個頂層 key、32 個布林旗標、15 個 `enable_*` |
| `main()` 約 556 行、約 60 個控制分支 | C §2.1 | **錯（低估）。** 637 行、88 個分支 |
| 「現有訊號機制已是足夠的逃生閥」，裝置生命週期 FSM 邊際收益很低 | **B §3.2/§3.4** | **最弱的一條。** 88 分支 + 12 個 `try` / 19 個 `except` / 1 個 `finally` 交錯，正是 C §2.2 所列問題（WS 階段收到強制休眠誰消費、暫停中收到手動開網頁誰優先）無權威答案的證據 |
| FSM 應優先於註冊表（C 的階段 1-3 先接管 `main()`，階段 4 才試作 registry） | **C §10** | **順序錯。** A §1.2 證明痛點密度在任務層（30 個任務 × 6 個關切），且 registry 可獨立交付、風險更低。C 的排序會讓最大的一筆投資走在最沒有安全網的地方 |

### 綜合結論（採納哪一份的哪一段）

1. **頂層任務序列不上 FSM** — A、B、C 三方共識，照辦。
2. **Task Registry 是第一優先** — 採 A 的排序理由 + B 的「單一真相來源」動機 +
   **C 的資料模型**（`TaskResult`/`TaskOutcome`/policy 物件，避免 A、B 版本的
   optional field 膨脹）。
3. **裝置生命週期窄 FSM 排第二** — 採 C §13 的最小試點 + shadow mode，
   駁回 B「列觀察清單就好」。但受 A §4 規則約束：試點必須能得出「不值得繼續」的結論。
4. **repo 衛生先做** — 採 B §5，但修正範圍：git 是乾淨的，要清的是工作目錄噪音。
5. **三後端收斂（WS-first / UI fallback）是最大槓桿** — A §5.1，登記在
   `TaskDefinition.executors`，與 registry 同一輪順帶完成。

### TODOLIST

#### 階段 0：衛生 + 事實校正（零風險，先做）

- [ ] 0.1 刪工作目錄 sync-conflict 噪音：`ws_token/lamp.sync-conflict-*.py`、
      `online_monitor`、`mining_adapter`、`equipment_scheme`、`battle/rogue_h5`、
      `.superpowers/`、`ws_state/*.json`、根目錄 `finish.sync-conflict-*`。
      **先確認 git 追蹤數為 0（已確認）**，故為純檔案刪除、不影響版控。
- [ ] 0.2 清 stale worktree（80 個：`.worktrees/` 26 + `worktree/` 8 + `.claude/worktrees/` 46）。
      **逐一確認已 merge 才刪**；`state-machine-registry-v2-gpt` 內含本次來源文件 C，先搬回主樹再刪。
- [ ] 0.3 移除未提交垃圾：`NUL`、`_check_devices.py`、`emu-test.json`。
      `web-001.json`～`web-004.json` 是 tracked/live ledger，禁止列入垃圾清理。
- [ ] 0.4 修 `daily_pipeline.py:171` docstring「20 tasks」→ 實際數（28）。
- [ ] 0.5 修正三份意見書的實測數字（記錄於本節「誰說錯了」，不改原文件）；
      `ws_state/` 與 git-tracked sync-conflict 兩項標記為**已解決**，勿重做。

#### 階段 1：特徵化測試（安全網，未完成前禁止搬 `_run_tasks`）

- [ ] 1.1 釘住 `_run_tasks` 現行 28 個任務的**順序**與**gating 條件**（flag / due / backend / ws_done）。
- [ ] 1.2 釘住隱性契約（A §2 清單，實測全部確認存在）：
      Task 4 `stage` 被 5/6 復用（`:300/:311`）；Task 18 後刷新 `stage` 供 Task 19（`:512`）；
      Task 12 限 20:00-23:00（`:404`）；Task 1 用 pipeline 開頭的 `current_time` 而非即時 `now`；
      `_DEVICE_SKIP_GUARDIAN` 5558（`:71`）；尾端 5558 / fc65396d 清理分支（`:253/:547`）。
- [ ] 1.3 **新增 `TASK_ORDER` ↔ `WS_TO_PIPELINE_SKIPS` ↔ `ws_done` 三方一致性測試**
      （目前零保護，名稱對不上會靜默漏做任務 — A §1.3 指出的最高風險）。
- [ ] 1.4 補 C §10 階段 0 的中斷情境測試：休眠中立即喚醒、WS 階段收強制休眠、
      暫停後收手動開網頁、手動結束後恢復原休眠、瀏覽器關閉不影響 ADB、
      三個時機點的異地登入、master 經 worker 下同一命令的等價性。

#### 階段 2：Task Registry（主投資，行為零變更）

- [ ] 2.1 定義資料模型（採 C §5.2 + A §2 欄位，見下方「Code Review 欄位」）。
- [ ] 2.2 定義 `TaskOutcome` / `TaskResult`，`INTERRUPTED` 與失敗分離（C §5.3）。
- [ ] 2.3 建資料表 + 讀取層，執行層先不動；跑階段 1 測試證明無行為變化（B §6.2）。
- [ ] 2.4 先遷 3 個代表性任務建模板（B §7 / C §10 共識）：
      ① 有 WS↔client 對照的（如 `lamp`）② 單一後端的 ③ 特殊 due/completion schema 的
      （`mission_timestamp` flat scalar、`farm_plant_click` dict — `ws_phase.py:98-123`）。
- [ ] 2.5 `_run_tasks` 收成單一迴圈（依 `order` 排序，6 個共用關切各做一次）。**純 code motion**。
- [ ] 2.6 pipeline 側產出 `RunReport`，與 WS 側同型。
- [ ] 2.7 `ws_phase.py` 兩張 dict + 兩個特例函式改由 registry 欄位推導。
- [ ] 2.8 8 個 scheduler 的 `_is_enabled/_is_due/_mark_done` 複製貼上收斂為 policy 物件。
- [ ] 2.9 AGENTS.md 補規範：新任務必須以 registry entry 註冊。
- [ ] 2.10 **重啟 `new_main_v2.py`**（`sys.modules` cache，改了不重啟等於白改 — A §2）。

#### 階段 3：Runtime FSM 最小試點（排在 registry 之後）

- [ ] 3.1 先拍板 C §12 的 8 個設計問題（見下方「待拍板」），未定案不動工。
- [ ] 3.2 純函式 `transition(context, event) -> TransitionDecision`，**不做 I/O**（C §8.2）。
- [ ] 3.3 試點範圍限 4 phase / 5 event（C §13）：
      `WS_PHASE / WAKING_CLIENT / CLIENT_TASKS / SLEEPING` ×
      `WS_COMPLETED / CLIENT_READY / TASKS_COMPLETED / WAKE_DUE / FORCE_SLEEP`。
- [ ] 3.4 `FORCE_SLEEP` 必須覆蓋全部 3 個活躍 phase；table-driven test 列出合法與非法轉移。
- [ ] 3.5 **shadow mode**：現有程式仍決定行為，新 transition 同步計算「應該去哪」，
      不同只記 log。先暴露模型遺漏，不改裝置行為。
- [ ] 3.6 **決策關卡**：若只是把相同數量的分支搬家 → **停止擴大**，保留 registry 成果。
      通過才接管中斷路徑（`FORCE_SLEEP` / `PAUSE` / `MANUAL_*` / `LOGIN_CONFLICT` / `SHUTDOWN`）。
- [ ] 3.7 不引入 FSM 套件；達到 C §12.8 門檻（階層狀態／狀態圖生成／自建開始重複套件）再評估 `transitions`。

#### 階段 4：三後端收斂（最大槓桿，registry 落地後）

- [ ] 4.1 以 `executors` 欄位盤點每個任務的 adb / h5 / ws 三份實作重疊。
- [ ] 4.2 逐任務收斂為「WS 優先，UI fallback」，砍掉冗餘實作（`_sea_dispatch` :76-85 為第一個目標）。
- [ ] 4.3 `RunReport` 上儀表板：本輪哪些任務跑了／被哪個條件跳過，從「讀 log」變「看表」。

#### 明確不做（YAGNI 護欄，三方共識）

- 不為頂層任務序列引入 FSM library / actor framework / asyncio 重寫。
- 不新建 orchestrator 框架；改造既有 `daily_pipeline`。
- 不同一輪同時動 UI 導航狀態機與任務註冊表。
- 不在階段 1 測試就位前搬 `_run_tasks`。
- 不把每個任務做成 class-based State Pattern（C §8.4）。
- 不讓 dashboard 顯示字串（`task="休眠中"`）反向推斷執行狀態（C §8.5）。
- 不讓 registry 兼任 scheduler（C §12.7，避免新的上帝物件）。

### 後續 Code Review 所需欄位與項目

#### A. `TaskDefinition` 建議欄位（三份合流版）

| 欄位 | 型別 | 來源 | Review 檢查點 |
|---|---|---|---|
| `task_id` | `str` | C §5.2 | **穩定 ASCII id，不可用中文顯示名當 key**（C 明確警告）。但 `ws_done` 現以中文字串比對，需 id↔display 對照層並保留舊行為 |
| `display_name` | `str` | 新增 | 中文顯示名與 `task_id` 分離；dashboard 徽章用此欄 |
| `order` | `int` | B/C | 取代 `13.5`/`14.65` 註解編號；用 int + 間隔（10/20/30）以便插入。Review：不得再出現小數 |
| `enabled_key` | `str \| None` | A/B/C | 對應 32 個布林旗標之一；`None` = 永遠開。Review：與 `DEFAULT_DEVICE_CONFIG` 對得上 |
| `due_policy` | `DuePolicy` | C §5.2 | 包 `task_due._REGISTRY` 現有 predicate，勿重寫 |
| `executors` | `Mapping[BackendKind, TaskExecutor]` | C | adb / web_h5 / ws 三後端；缺哪個要明示。**階段 4 收斂的登記處** |
| `completion_policy` | `CompletionPolicy` | C | 吸收 `SKIP_TO_DAILY_RECORD` + `mission_timestamp` flat scalar + `farm_plant_click` dict 三種 schema |
| `skip_when_ws_done` | `bool \| tuple[str,...]` | A/B | 取代 16 處內聯 `_ws_skip`；來源 `WS_TO_PIPELINE_SKIPS` |
| `needs_main_page` | `bool` | A §2 | 取代 13 處 `_guarded_run`；Task 9 為 `False`（no main-page guard，`:371`） |
| `record_name` | `str \| None` | A §2 | `time_recording` 名稱 |
| `timeout_sec` | `float \| None` | C | 收 `_ROGUE_PROBE_S`、`_TREASURE_PROBE_S`（6s）等特例常數 |
| `retry_policy` | `RetryPolicy` | C | 預設 none |
| `time_window` | `tuple[int,int] \| None` | 實測補 | Task 12 限 20:00-23:00（`:404`）— 三份都沒給欄位，但 code 裡有 |
| `device_excludes` | `frozenset[str]` | 實測補 | `_DEVICE_SKIP_GUARDIAN`（5558 跳守護靈/技能夥伴，`:71`）|
| `batch_cap` | `int \| None` | B §4.2 | `_LAMP_BATCH_NUM` 等批次上限 |
| `tags` | `frozenset[str]` | C | 分類/篩選 |

**Review 紅線（C §8.6）**：每加一個任務就要加一個 optional field → 抽象邊界錯了。
三個以上任務共用的差異才建模為 policy；單一任務專屬細節留在 executor 內。

#### B. `TaskResult` / `TaskOutcome`（C §5.3，A、B 皆缺）

| 欄位 | Review 檢查點 |
|---|---|
| `outcome` | `COMPLETED / SKIPPED / RETRYABLE_FAILURE / PERMANENT_FAILURE / INTERRUPTED` |
| `detail` | 人可讀原因；進 `RunReport` |
| `retry_after_sec` | 僅 `RETRYABLE_FAILURE` 有意義 |
| `completion_updates` | 要回寫的 ledger key→value |

**最重要的一條**：`INTERRUPTED`（使用者強制休眠）**不得**污染錯誤統計或觸發重試，
也**不得**被誤記為完成（C §12.4）。Review 時逐一確認每個 executor 的回傳映射。

#### C. Runtime FSM Review 欄位（階段 3 才用）

| 項目 | 檢查點 |
|---|---|
| `RuntimePhase` | 數量 ≤ 8-10（C §8.1）；確認沒把 control mode / observation / task progress / retry counter 誤當 phase |
| `ControlMode` | `RUNNING / PAUSED / MANUAL` 為**正交欄位**，不進 phase enum（避免 `PAUSED_WS_PHASE` 狀態爆炸） |
| `RuntimeEvent` | 每個 event × 每個 phase 都有明確策略：允許／忽略／延後／拒絕 |
| 事件優先級 | 候選 `SHUTDOWN > FORCE_SLEEP > LOGIN_CONFLICT > MANUAL_LAUNCH > PAUSE > WAKE_OVERRIDE`（**待拍板**）；低優先事件是丟棄、保留或回報 conflict |
| `transition()` 純度 | 不連 ADB / 不開瀏覽器 / 不連 WS 即可測（C §8.2）；只回 effect intent |
| 非法轉移 | 不得靜默改狀態；須拒絕並記錄 |
| 結構化 log | 每次轉移有 `device / from / event / to / reason / timestamp` |
| 任務不得直接改 phase | 禁止 `runtime.phase = SLEEPING`；只回報結果，由單一 `dispatch()` 決定（C §3.4） |
| dashboard 投影方向 | typed context → snapshot，**不可反向**（C §8.5） |
| 持久化分層 | 可重建（phase / connection / observation）／需持久（ledger、cooldown deadline、resume intent）／僅顯示（task/step 文字）（C §12.6） |

#### D. 每個 PR 通用 Review 項目

- [ ] 行為零變更：階段 1 特徵化測試全綠（**不得同 PR 改測試預期值**）。
- [ ] 只 stage 有動到的檔案（絕不 `git add -A`：~80 WIP 檔 + `auth_state/` secrets）。
- [ ] worktree 隔離；完成後 merge 回 main 再刪 worktree + branch。
- [ ] 目標測試 + `py_compile` 通過；禁裸 `pytest`（會 import 真實 device/Playwright/OCR 而 hang）。
- [ ] 熱路徑改動已重啟 `new_main_v2.py`（無 hot-reload）。
- [ ] 讀 JSON/py 用 `encoding="utf-8-sig"`（多數檔帶 BOM）。
- [ ] 新 opt-in 功能**必須有 dashboard 控制項**，不可只有 config（既有慣例）。
- [ ] `_WEB_DEVICE_LOCK` 保持 RLock，不得降級。
- [ ] 新抽象的**唯一消費者是 live 路徑**（A §4：`farm_v2/states.py` 已移除、`task_sandbox/`
      與 `bootstrap/api_services.py` 至今未接線 — 實測確認；`page_detector` 已接線，A 此處誤判）。

### 拍板決議（2026-08-09，依現有程式碼實證裁定）

**裁定原則：現有程式碼已經用行為回答了大半問題。凡是 code 裡已有可運作語意的，
一律「形式化既有行為」，不重新發明。** 以下 6 題全部定案，階段 3 可動工。

#### 1. 暫停 = control mode（不是 phase）✅ 定案

證據：`bot_state.check_pause()`（`:320`）是**阻塞式**的 — thread 卡在 `event.wait(1.0)`
迴圈直到恢復，恢復後原地繼續。這本質上就是 control mode：phase 不變，只是暫時凍結。
若做成 phase 就必須記「暫停前在哪」再跳回去，等於把現成語意複雜化。

- **暫停 WS 是原地續跑還是重跑本輪 ledger？** → **原地續跑，靠既有 `ws_resume` ledger**
  （`ws_phase.py:228` `_RESUME_KEY`，TTL 30 分 + 同日，逾時/隔日視為 stale 全跑；
  `_RESUME_EXEMPT = {carpark, idle_reward}` 這兩個累積收益/時間窗任務 resume 仍重跑）。
  **不要新設計 resume 機制**，這套已有 spec 與測試（`tests/test_ws_phase_resume.py`）。
- **暫停 client task 可否從當前 task 續？** → **可以，且已是現況**：`check_pause()`
  在任務邊界阻塞，恢復後續跑同一輪。registry 化後 checkpoint 位置不變。
- **暫停期間是否保留瀏覽器與 WS session？** → **保留**。現況阻塞在原地、資源未釋放，
  且暫停常是為了手動接管看畫面，關掉反而違反使用者意圖。

#### 2. 手動接管 = 獨立 phase（`MANUAL`）✅ 定案

C 傾向 phase，這點對，理由在 code：手動接管有**明確的資源進出動作**
（`request_web_launch` / `consume_web_launch_request` / `complete_web_launch_request` /
`set_manual_release`，`bot_state.py:763-848`），而且 `check_pause()` 內部**特地為它開後門**
（`:333` 偵測 `has_pending_web_launch_request` 就 `return True` 讓它插隊）。有獨立資源
生命週期 + 能打斷 pause = phase，不是 mode。

- **退出後 resume policy 從休眠 vs 從 client task 進入是否一樣？** → **一樣，統一回休眠**。
  現況兩處都寫 `resume_sleep_reason = "手動操作結束後返回休眠"`
  （`new_main_v2.py:435` 與 `:686`）— 已經是一致的，形式化時保留。

#### 3. 事件優先級 ✅ 採納候選順序，一處修正

```text
SHUTDOWN > FORCE_SLEEP > LOGIN_CONFLICT > MANUAL_LAUNCH > PAUSE > WAKE_OVERRIDE
```

`MANUAL_LAUNCH > PAUSE` 與 `check_pause():333` 的既有後門一致，非新設計。
`LOGIN_CONFLICT` 排在 `MANUAL_LAUNCH` 前的理由：異地登入會強制 30 分休眠
（既有 `mark_login_conflict_sleep`），此時開瀏覽器只會再撞一次衝突。

- **丟棄政策** → **三分法**：`SHUTDOWN`/`FORCE_SLEEP`/`LOGIN_CONFLICT` 為
  **latching**（一旦收到必須被消費，不可丟）；`MANUAL_LAUNCH` **保留到新 phase**
  （已有 pending/consume/complete 三段式，天生 latching）；`WAKE_OVERRIDE` 在
  非 `SLEEPING` phase **直接丟棄**（已經醒著，喚醒無意義）並記 log，不回報 conflict。

#### 4. 中斷 = cooperative cancellation ✅ 定案，且已實作

**不需要新機制** — `runner.py` 已把 `should_abort` callable 貫穿到每個任務
（`:1820` "polled at every task boundary"、`:1936` 主迴圈檢查，`aborted` 時
剩餘任務留 **pending** 而非 failed）。這正是 C §5.3 要的 `INTERRUPTED` 語意，已存在。

- **最大允許多久回應強制休眠？** → **以任務邊界為單位，目標 ≤ 60 秒**；
  超過 60 秒的單一呼叫（戰鬥模擬、mining plan、OCR 等待）必須自帶可取消 timeout。
  不設更嚴格的數字，因為 Python thread 不可強制 kill，硬性 SLA 只會逼出 busy-wait。
- **哪些長呼叫必須可取消？** → 已有 timeout 常數的（`_ROGUE_PROBE_S`、`_TREASURE_PROBE_S`、
  `_HUMAN_WAIT_POLL_SEC`、`_UNDETERMINED_MAX_POLLS`）沿用；新增任務若單步 > 60 秒必須傳 `should_abort`。
- **被中斷時如何寫 ledger？** → **一律寫 pending / `INTERRUPTED`，絕不寫完成**
  （現況 `aborted` 已如此）。這是階段 2.2 的 review 紅線。

#### 5. 休眠 = 保留 `SLEEPING` phase，但等待由 scheduler 做 ✅ 定案

理由與 C 一致且有 code 支持：休眠是可觀測（dashboard 顯示 next wake）、可中斷
（`SKIP_SLEEP`）、有 `resume_sleep_until_ts` 的長期階段（`new_main_v2.py:186`）。
但 `transition()` 必須是純函式，**不得阻塞** — 等待留在既有 `run_sleep_cycle()` effect 內。

#### 6. 是否凍結新功能一週 ❌ 不凍結，改用「表格優先」規則

凍結一週對這個 repo 不現實（單人 + 多 AI session 併行 + bot 每天在跑）。
改採較弱但可執行的護欄：**階段 2 期間新增任務一律直接寫成 registry entry**，
不再往 `_run_tasks` 插小數編號。這樣新功能反而變成 registry 的驗證案例，而非衝突源。
搭配 worktree 隔離 + 階段 1 的一致性測試，衝突風險可控。

### 設計邊界（防過度設計；本節為硬約束，違反者 review 直接退回）

你的疑慮成立，而且**這個 repo 有可驗證的過度設計前科**：`farm_v2/states.py`
（狀態機，2026-07-05 移除，從未接線）、`task_sandbox/`（實測 0 個 live 消費者）、
`bootstrap/api_services.py`（實測只有測試在 import）。三個抽象、三個沒進 live path。
所以邊界不是理論潔癖，是針對已發生兩次的失敗模式。

#### B0. 唯一鐵則：先接消費者，後建框架

任何新模組的**第一個 PR 就必須有 live 路徑消費者**。不允許「先建框架，下一個 PR 接線」——
這正是上述三個死抽象的產生方式。無法在同一個 PR 內接上 live 路徑的抽象，**不要建**。

#### B1. 數量預算（超過即停下重新評估，不得靜默放寬）

| 項目 | 上限 | 超過時的意義 |
|---|---|---|
| `RuntimePhase` 成員 | 10 | 可能把 control mode / observation / retry counter 誤當 phase |
| `RuntimeEvent` 成員 | 12 | 可能把「任務結果」當成 runtime 事件 |
| `TaskDefinition` 欄位 | 18 | 抽象邊界錯了，該收成 policy |
| 新增檔案數（階段 2） | 6 | registry 不該長成一個子系統 |
| 新增檔案數（階段 3 試點） | 3 | 試點就該是 3 個檔案能講完的東西 |
| 單檔行數 | 400（硬上限 800） | 既有專案規範 |
| policy 類別數 | 5 | `DuePolicy`/`CompletionPolicy`/`RetryPolicy` + 最多 2 個 |

#### B2. 禁止清單（明確不做）

- **禁止**為頂層任務序列引入 FSM library / actor framework / asyncio 重寫。
- **禁止**引入任何新的第三方套件（含 `transitions`）— 階段 3 用純函式 + Enum。
  要引入必須先達 C §12.8 門檻並單獨提案。
- **禁止**把任務做成 class-based State Pattern（一堆只包一個 `run()` 的小 class）。
- **禁止**新建 orchestrator / engine / framework 命名的模組。改造既有 `daily_pipeline`。
- **禁止**同一輪同時動 UI 導航狀態機與任務註冊表。
- **禁止**為「未來可能的任務」預留欄位 / hook / plugin 機制（YAGNI）。
- **禁止**用 `dict[str, Any]` 當設定容器取代可驗證的 dataclass。
- **禁止**把大量 lambda 與隱性副作用塞進資料表。
- **禁止**讓 registry 兼任 scheduler（避免新的上帝物件）。
- **禁止**讓 dashboard 顯示字串反向推斷執行狀態（方向只能 context → snapshot）。
- **禁止**在 `transition()` 內做 I/O（不連 ADB / 不開瀏覽器 / 不連 WS 即可測）。
- **禁止**強迫 WS / ADB / Playwright 三個實作長得一樣；統一的只有輸入輸出合約。

#### B3. 抽象正當性三問（每個新類別/協議都要能回答）

1. **現在有幾個真實呼叫點？** 少於 2 個 → 不要抽象，直接寫在使用處。
2. **移除它會讓程式碼變長還是變短？** 變長才留。變短或持平 → 是儀式，不是抽象。
3. **它能在不啟動 bot 的情況下被測試嗎？** 不能 → 邊界劃錯了。

#### B4. 每階段的「不值得繼續」出口（必須真的可以喊停）

- **階段 2 出口**：若遷完 3 個代表性任務後，`TaskDefinition` 已需要第 4 個
  單一任務專屬欄位 → 停止擴大，只保留這 3 個 + 一致性測試（1.3）的成果。
- **階段 3 出口**：若 shadow mode 跑滿一週，transition 模型與實際行為的分歧
  **全部**是模型漏設而非真實 bug → FSM 沒有發現任何東西，**放棄階段 3**，
  保留 registry。這個結論是合法且可接受的產出。
- **階段 4 出口**：若某任務三後端收斂會讓行為出現不可測的差異 → 該任務維持三份實作，
  只在 registry 登記，不強行合併。

#### B5. 判定「這是過度設計」的即時信號

- 寫了一個介面，但只有一個實作，且看不到第二個。
- 為了測新抽象，必須先寫 50 行 mock。
- 解釋這個設計需要畫圖，而它取代的舊 code 不需要。
- 新增一個任務時，要碰的檔案數**沒有變少**（registry 的唯一目的就是讓它變少）。
- 出現 `Base*` / `Abstract*` / `*Manager` / `*Factory` 命名而該層只有一種行為。

### 可分派 TODOLIST（含併行標記）

**圖例**：`[P-n]` = 併行群組 n，同群組內可同時派給不同 agent／session；
`[SEQ]` = 必須等前置完成。每張工單標了 **owned files**（該 agent 唯一可寫的檔案），
避免多 session 互相覆寫。全部走 worktree 隔離。

> 依賴總覽：`W1 ∥ W2 ∥ W3` → `W4 ∥ W5 ∥ W6` → `W7` → `W8 ∥ W9 ∥ W10` → `W11` → `W12` → `W13 ∥ W14`

---

#### 波次 1：衛生 + 事實校正（3 張工單全可併行）

- [x] **W1 [P-1] 清工作目錄 sync-conflict 噪音**
  - owned：無原始碼（純刪檔）
  - 範圍：`ws_token/*.sync-conflict-*`、`game_actions/equipment_scheme.sync-conflict-*`、
    `battle/rogue_h5.sync-conflict-*`、`dragon_realm/client.sync-conflict-*`、
    `.superpowers/*.sync-conflict-*`、`ws_state/*.sync-conflict-*.json`、
    `docs/**/*.sync-conflict-*`、根目錄 `finish.sync-conflict-*`、`_wf_indexDoc.sync-conflict-*`
  - 前置確認：git 追蹤數為 0（已驗證），故純檔案刪除
  - 驗收：`find . -name "*sync-conflict*" -not -path "./.git/*" --exclude vendored` 在原始碼目錄歸零
  - **注意**：`.ruff_cache/` 與 `logs/` 內的可一併刪但非必要；勿刪 `.conda/`、`OCR/` 內 vendored
  - **保留**：`playwright_profile/` 內的 Cookies、Login Data、Local State、Preferences、session/auth
    與任何 `sync-conflict` 副本都是登入驗證資料，不得移動或刪除。

- [x] **W2 [P-1] 清 stale worktree + 未提交垃圾**
  - owned：`.gitignore`
  - 範圍：80 個 worktree（`.worktrees/` 26、`worktree/` 8、`.claude/worktrees/` 46）
    **逐一確認已 merge 才刪**；`NUL`、`_check_devices.py`、`emu-test.json`
  - **保留資料**：`web-001.json`～`web-004.json` 是紀錄檔；cookies、登入驗證、`auth_state`、session 與 browser profile 一律不列入垃圾清理。
  - **必做前置**：`.worktrees/state-machine-registry-v2-gpt/狀態機與註冊表說明意見v2-gpt.md`
    先搬回主樹（本次分析來源文件）再刪該 worktree
  - 驗收：`git worktree list` 只剩 in-flight 的；`git status` 無上列垃圾

- [x] **W3 [P-1] 文件事實校正**
  - owned：`game_actions/daily_pipeline.py`（僅 docstring 一行）、`docs/INDEX.md`、`CLAUDE.md`
  - 範圍：① `daily_pipeline.py:171` docstring「20 tasks」→ 28
    ② `CLAUDE.md` / `docs/INDEX.md` 若記載 `page_detector` 為 experimental 未接線 → 更正為
    **已接線**（`stage_guard.py:45`、`game_initialization.py`、`utils/cocos_navigator.py`，per-device flag-gated）
  - 驗收：`py_compile` 通過；無行為變更

---

#### 波次 2：特徵化測試安全網（3 張併行；W4 為最高優先）

- [x] **W4 [P-2] ⭐ `TASK_ORDER` ↔ `WS_TO_PIPELINE_SKIPS` ↔ `ws_done` 三方一致性測試**
  - owned：`tests/test_ws_pipeline_consistency.py`（新檔）
  - **這是目前零保護的最高風險項，且可獨立於整個 registry 交付 — 建議第一個做**
  - 範圍：斷言 `WS_TO_PIPELINE_SKIPS` 的 key 全在 `TASK_ORDER`（40 項）內；
    value 的中文任務名全部能對上 `_run_tasks` 內實際使用的 `_ws_skip(...)` 字串（16 處）；
    `SKIP_TO_DAILY_RECORD` 的 key 全在 `WS_TO_PIPELINE_SKIPS` 的 value 集合內
  - 驗收：故意改錯一個字串，測試必須紅

- [x] **W5 [P-2] `_run_tasks` 順序與 gating 特徵化測試**
  - owned：`tests/test_daily_pipeline_order.py`（新檔；**勿改** `tests/test_daily_pipeline.py`）
  - 範圍：釘住 28 個任務的執行順序與 4 種 gating（flag / due / backend / ws_done）；
    以 fake `DailyContext` + 記錄呼叫序列的 spy 實作，不連真實 device
  - 隱性契約（實測確認，必須有測試）：Task 4 `stage` 被 5/6 復用（`:300/:311`）；
    Task 18 後刷新 `stage` 供 Task 19（`:512`）；Task 12 限 20:00-23:00（`:404`）；
    Task 1 用 pipeline 開頭 `current_time` 而非即時 `now`；`_DEVICE_SKIP_GUARDIAN` 5558（`:71`）；
    尾端 5558 / fc65396d 清理分支（`:253/:547`）
  - 驗收：測試在**未改動** `daily_pipeline.py` 的情況下全綠（這是基線，不是 TDD 紅燈）

- [x] **W6 [P-2] 中斷情境特徵化測試**
  - owned：`tests/test_runtime_interrupts.py`（新檔）
  - 範圍：休眠中收立即喚醒、WS 階段收強制休眠（驗證剩餘任務留 **pending** 非 failed）、
    暫停後收手動開網頁（驗證 `check_pause():333` 後門插隊）、手動結束後恢復原休眠
    （`new_main_v2.py:435/:686` 兩路徑同語意）、瀏覽器關閉不影響 ADB 裝置、
    初始化/WS fallback/運行中三時機的異地登入、master 經 worker 下同一命令的等價性
  - 驗收：全綠且不連真實 ADB/Playwright/WS

---

#### 波次 3：registry 資料模型（單張，需 W4-W6 全綠）

- [x] **W7 [SEQ] 定義 `TaskDefinition` / `TaskOutcome` / `TaskResult` + 讀取層**
  - owned：`game_actions/task_registry.py`（新檔）、`tests/test_task_registry.py`（新檔）
  - 範圍：資料模型（欄位見上方「Code Review 欄位」A 表，**上限 18 欄**）+ 三個 policy 物件
    + registry 表；**執行層完全不動**
  - `DuePolicy` 必須包裝既有 `task_due._REGISTRY` predicate，**不重寫 due 邏輯**
  - `TaskOutcome`：`COMPLETED / SKIPPED / RETRYABLE_FAILURE / PERMANENT_FAILURE / INTERRUPTED`
  - **B0 鐵則**：本 PR 必須至少有一個 live 消費者（可先讓 W4 的一致性測試改讀 registry）
  - 驗收：W4-W6 全綠（證明零行為變更）；`TaskDefinition` 欄位數 ≤ 18

---

#### 波次 4：遷 3 個代表性任務（3 張併行，各自 owned 不重疊）

> 三張都只改 registry 表的**自己那幾列** + 自己的 executor 檔，**禁止**任何人改 `_run_tasks` 主體。

- [x] **W8 [P-4] 遷「有 WS↔client 對照」任務（`lamp` 開神燈）**
  - owned：`game_actions/executors/lamp_executor.py`（新檔）、`tests/test_lamp_executor.py`
  - 重點：`skip_when_ws_done` 欄位取代內聯 `_ws_skip("開神燈")`；`batch_cap` 收 `_LAMP_BATCH_NUM`
  - 保留隱性契約：Task 19 依賴 Task 18 刷新過的 `stage`
  - 完成：`6187bd55`；未修改 `_run_tasks`／`ws_phase.py`

- [ ] **W9 [P-4] 遷「單一後端」任務（Task 14.5 龍骸聖域 或 Task 14.6 煩惱消，H5 only）**
  - owned：`game_actions/executors/single_backend_executor.py`（新檔）+ 對應測試
  - 重點：驗證 `executors` 只登記 `web_h5` 時，adb 裝置**乾淨跳過**而非 abort
  - 阻塞（2026-08-09）：`fannaoxiao` 是 client-only registry row，目前沒有 live consumer 讀取其 executor；新增 adapter 會形成死抽象，待 W11 接線範圍明確後再做。
  - 審查更正（2026-08-09）：上述理由**不足以單獨排除 W9** — W8（lamp）與 W10（farm）
    的 executor 同樣沒有 live consumer（`grep` 主樹確認：僅 registry 字串與測試引用，
    `daily_pipeline` / `ws_phase` 都沒 import），三者的死抽象風險等級相同。真正的差別是
    W8/W10 是薄轉接層（呼叫既有 `LampService` / `farm_v2.manager`），而 W9 要驗的
    「adb 裝置乾淨跳過」語意在 W11 之前無處可驗。**結論：暫緩成立，但理由要改成
    「缺可驗證的 skip 路徑」，不是「唯一沒有 consumer」。** W11 接線時三個 executor
    必須同批接上，否則 B0 規則對 W8/W10 一樣不成立。

- [x] **W10 [P-4] 遷「特殊 due/completion schema」任務（農場 / 每日任務）**
  - owned：`game_actions/executors/farm_executor.py`（新檔）+ 對應測試
  - 重點：`CompletionPolicy` 要能同時表達 `mission_timestamp`（flat scalar）與
    `farm_plant_click`（dict）兩種 schema（`ws_phase.py:98-123`），**不新增 optional field**
  - 這張是 **B4 階段 2 出口的判定點**：若表達不了，喊停
  - 完成：`8e57ebbb`；12 個 executor／一致性測試與 11 個 pipeline order 測試通過

---

#### 波次 5：收斂主迴圈（單張，高風險）

- [ ] **W11 [SEQ] `_run_tasks` 收成單一迴圈 + `RunReport`**
  - owned：`game_actions/daily_pipeline.py`、`game_actions/ws_phase.py`
  - 範圍：依 `order` 排序的單一迴圈，6 個共用關切各做一次（取代 28×force_sleep、
    16×ws_skip、13×guarded_run、8×update_state、4×time_recording）；
    pipeline 側產出與 WS 側同型的 `RunReport`；`ws_phase` 兩張 dict 改由 registry 推導
  - **純 code motion，行為零變更**
  - 驗收：W4/W5/W6 全綠且**未修改測試預期值**；改完**重啟 `new_main_v2.py`**（無 hot-reload）
  - 高風險：這是 live bot 熱路徑，建議單獨一個 session、單獨 review

---

#### 波次 6：後續（2 張併行，各自有獨立出口）

- [ ] **W12 [SEQ, 需 W11] 8 個 scheduler 的 `_is_enabled/_is_due/_mark_done` 收斂為 policy**
  - owned：`game_actions/*_scheduler.py`（8 檔）
  - 驗收：每個 scheduler 的既有測試全綠；行為零變更

- [ ] **W13 [P-6] Runtime FSM 最小試點（shadow mode，不接管行為）**
  - owned：`runtime_services/runtime_fsm.py`（新檔）、`tests/test_runtime_fsm.py`
  - 範圍嚴格限定：4 phase（`WS_PHASE / WAKING_CLIENT / CLIENT_TASKS / SLEEPING`）
    × 5 event（`WS_COMPLETED / CLIENT_READY / TASKS_COMPLETED / WAKE_DUE / FORCE_SLEEP`）
  - 依上方拍板決議實作：pause = control mode（正交欄位）、manual = 獨立 phase、
    優先級 `SHUTDOWN > FORCE_SLEEP > LOGIN_CONFLICT > MANUAL_LAUNCH > PAUSE > WAKE_OVERRIDE`、
    `WAKE_OVERRIDE` 非 SLEEPING 時丟棄
  - `transition()` 純函式、零 I/O；`FORCE_SLEEP` 覆蓋全部 3 個活躍 phase；
    table-driven test 列出合法與非法轉移；fake effect executor
  - **shadow mode**：只記 log 不改行為，跑滿一週
  - **B4 出口**：若分歧全是模型漏設而非真實 bug → 放棄階段 3，這是合法產出
  - 上限：新增檔案 ≤ 3、phase ≤ 10、event ≤ 12、不引入任何套件

- [ ] **W14 [P-6] `RunReport` 上儀表板**
  - owned：`control_panel/routes_status.py` + 對應前端 template
  - 範圍：本輪哪些任務跑了／被哪個條件（flag/due/backend/ws_done）跳過，從「讀 log」變「看表」
  - 依既有慣例：新功能必須有 dashboard 控制項，不可只有 config

---

#### 派工建議

- **想最快見效、只做一件事** → **W4**（零保護的最高風險，可獨立交付，不動任何生產碼）。
- **可同時開 3 個 session** → 波次 1 全部（W1/W2/W3 互不衝突）。
- **W11 不要與任何工單併行**：它改 live bot 熱路徑，且 W8-W10 的 executor 檔案是它的輸入。
- **W13 可全程與波次 4-6 併行**：shadow mode 不改行為，唯一寫的是新檔 + log。
- 每張工單完成即 commit（只 stage owned files，絕不 `git add -A`），
  驗收通過後 merge 回 main 並刪 worktree + branch。





1. **暫停是 phase 還是 control mode？** 傾向 control mode（恢復時保留原 phase）。
   需確認：暫停 WS 是原地續跑還是重跑本輪 ledger？暫停 client task 可否從當前 task 續？
   暫停期間是否保留瀏覽器與 WS session？
2. **手動接管是 control mode 還是獨立 phase？** 可能需獨立資源進出動作，比 pause 更像 phase。
   從休眠 vs 從 client task 進入，退出後 resume policy 是否一樣？
3. **事件優先級與丟棄政策**（上表候選順序是否採納）。
4. **中斷為 cooperative cancellation**：最大允許多久回應強制休眠？哪些長呼叫必須可取消？
5. **休眠是 phase 還是排程器等待？** 傾向保留 `SLEEPING` phase，但等待由 scheduler 實作，
   不讓 state machine 阻塞。
6. **是否凍結新功能一週**以避免遷移途中併入新任務（B §6 最大風險）。





## 2026-08-08 所有裝置預設走萬神 pure WS until-cap

- [x] 預設設定已是 `wanshen_battle_mode=pure_ws`、`wanshen_until_cap=true`。
- [x] 現有明確覆寫已統一為 pure_ws/true。
- [x] 修正 ADB 分派：pure WS 成為所有 backend 主路徑，失敗才回退 OCR。
- [x] 補 ADB 預設 pure WS 且達上限不進 OCR 戰鬥的回歸測試。

### Review

- web_h5 與 ADB 都會讀同一 device config；pure WS 成功時共用 `0x4C16` until-cap。
- fallback 維持原行為：web_h5 animation、ADB OCR，不影響故障時可用性。
- until-cap 完成條件只接受權威 `cap_reached`；fallback 跑滿安全局數不會誤寫本週完成。
- 驗證：萬神 WS/排程/設定/runtime/API 共 88 passed；dashboard 三項預設測試 3 passed；
  目標 `py_compile` 通過。dashboard 全檔另有 1 個中文亂碼斷言失敗，已在未修改 main 重現。

## 2026-08-08 萬神 pure WS 刷到本周獲取上限

- [x] 5556 CDP 確認神樹祝福 `costTips/limit` 顯示 `5000/5000`。
- [x] 純 WS live 確認 `0x4C16` field 2/3 對應 UI current/cap。
- [x] 新增 pure WS 上限解析與 until-cap 循環。
- [x] 補目標測試並驗證。

### Review

- 權威來源：`rogue_science_info_s2c` 的 `science_point#2` / `point_max#3`；5556
  live 回應 `5000/5000`，與 `RogueScienceView/content/costTips/limit` 完全一致。
- pure WS 會在開跑前與每局結束後複讀；達標即正常完成，無進度或安全局數耗盡則不誤寫完成。
- fail-closed：until-cap 讀不到 `0x4C16` 時不開新局，交由既有 H5 fallback 重讀 UI。
- 驗證：rogue WS、weekly orchestration、special wanshen runtime/API 共 64 passed；目標
  `py_compile` 通過。

> 已完成項目（含 Review）已移至 `finish.md`（2026-06-20 archive 區塊）。
> 重構真相來源：`docs/REFACTORING_OPPORTUNITIES.md`。
> 其他檔案各自追蹤：見末段。

---

## 🚧 2026-07-29 final_v1 鑽頭可視底列擴散修復

- [x] 從 5558 15:39 紀錄確認：WS 已知地形擴充後，鑽頭左右擴散被算到整張已知盤面底列。
- [x] 新增 15 列已知盤面、7 列可視區的鑽頭相鄰欄收礦回歸測試。
- [x] 修正 final_v1 以可視區底列計算鑽頭左右擴散，且不計畫面外鑽頭收益。
- [x] 執行目標 pytest、py_compile 與 diff 審查。

### Review

- 根因：`_affected()` 把完整 WS 已知盤面的列數傳給鑽頭 mechanics，再截掉畫面外格；因此左右擴散
  落在已知盤面底列並被截掉。修正後鑽頭 mechanics 直接收到 `min(visible_rows, rows)`。
- 回歸：15 列盤、可視 7 列、礦在 `(6,3)`、鑽頭放 `(1,2)`，現在可由底列右擴散收礦。
- 驗證：`test_final_v1_planner.py`、adapter、service 共 39 passed；目標 `py_compile` 通過；
  `git diff --check` 僅有既有 Windows LF/CRLF 提示。

### 新發現待辦（5558 2026-07-29 15:35-15:40 log）

- [ ] **高優先：修正 web_h5 挖掘驗證來源。** 本輪 13 次 `verify_fail`；action trace 顯示點擊後已有
  `0x0c03/3075` 回應，但 `verify_cell_empty()` 仍依 CNN 截圖判空，誤判後中止多步 plan。web_h5 應優先用
  WS 挖礦回應或系統既有 board signature 確認，CNN 只作 fallback；ADB 維持視覺驗證。
- [ ] **高優先：修正 verify retry 的鏟子計帳。** executor 每次補點都無條件 `shovels_used += 1`，即使目標
  已挖空、補點未消耗鏟子也會多扣。此輪多次出現內部比 OCR 少 2（`15→OCR 17`、`5→OCR 7`），
  與一次原點擊加一次補點的假扣款吻合；應以 WS `0x0402` 庫存事件/前後現量確認，ADB 才保守估算。
- [ ] 補 executor telemetry：每次 `verify_fail` 記錄目標、CNN 前後 label/confidence、WS action ack、
  retry 前後鏟子現量，區分真點擊失敗、動畫未穩定與分類誤判。

### 純 WS 路徑補修（使用者指正後）

- [x] 追查 `mining_supervised -> mining_adapter.plan -> final_v1`，確認純 WS 也會執行 final_v1，且
  `_select_dig_step()` 在 planner 前另有 `prop_step_for_pit()` 快速道具路徑。
- [x] 修正純 WS `_drill_clear_cells()`：絕對深度轉 7 列相對座標後重用
  `miner.core.mechanics.get_drill_affected_cells()`，恢復可視底列左右擴散並禁止畫面外收益。
- [x] 限制純 WS bomb/drill 放置點只能位於可視 7 列；WS 傳來的畫面外 count==0 block 不再當落點。
- [x] 修正建立在舊錯誤物理上的 combo fixtures；新增底列相鄰欄命中、畫面外同欄不命中、畫面外
  空格不可放道具三項回歸。
- [x] 驗證：純 WS prop combo、adapter、supervised loop、final_v1、service 共 83 passed；
  目標 `py_compile` 通過；`git diff --check` 僅 Windows LF/CRLF 提示。
- [x] 合併 `main` 後用 7fe98fc6 CDP 9226 跑正式 live 驗證：planner 與 supervised selector
  都自行選出 `drill -> (row=3,col=4)`；庫存 `7→6`、baseline `174435→174436`，WS 前後盤面確認
  可視底列 `(row=6,col=3/4/5)` 全部變化，包含右側相鄰欄礦物 `col=5`，證明底列 `1×3` 生效。

### Live 追加觀察

- [x] `_board_confirmation()` 的 `footprint_changed` 歸因偏寬：驗證腳本在鏟子已歸零後誤送鎬子時，
  其他背景盤面變化曾被判 confirmed；production `mine_until_pickaxe_empty()` 有 `pickaxe<=0` 前置閘門，
  本次正式流程不受影響。已改成只接受 baseline、目標格、道具預期足跡或對應庫存的可歸因變化；
  dig 的無關盤面變化不再算成功，炸彈保留畫面外足跡、鑽頭限制可視 7 列。
- [x] `_select_dig_step()` 單獨呼叫不檢查鎬子庫存，可能在 `pickaxe=0` 時把 final_v1 的合法道具步
  覆寫成 dig。已在 planner steps、一般 fallback、below-pit steering 三個出口加入已知庫存歸零防呆；
  舊 caller 未傳 inventory 時維持既有相容行為。

### H5 WS 事件化驗證修復

- [x] 新增 `read_ws_mine_board()`，H5 executor 點擊前後直接讀 JavaScript 內 `0x0c01` 盤面；
  同時用 `0x0401` 現量確認鎬子/鑽頭/炸彈是否實際扣除。
- [x] H5 挖步只要 WS 可用就不再呼叫 CNN `verify_cell_empty()`，也不會因 CNN 動畫誤判而補點；
  ADB/WS 不可用才維持 CNN fallback。
- [x] H5 鏟子計帳改為 authoritative `pickaxe_count_after`，`_apply_partial()` 直接同步 WS 現量，
  不再把未消耗的補點無條件算成一支鏟子。
- [x] 第 7 列下樓步也走相同 WS 驗證與庫存計帳，不再無條件視為成功。
- [x] telemetry 補上 verification events：WS/CNN 來源、confirmation、前後庫存、CNN label/confidence。
- [x] 驗證：相關 executor/service/final_v1/adapter/純 WS 共 118 passed，目標 `py_compile` 通過。
- [x] H5 動作執行也改為 JavaScript `MysteryControl.reqMineUseGoods()`：鎬子/鑽頭/炸彈不再走像素
  選道具與點格子；ADB 維持原點擊路徑。首次 live 用舊像素路徑被新 WS 驗證正確拒絕且未扣道具，
  改成 JS dispatch 後同一 planner `drill -> (row=0,col=4)` 成功：baseline `174436→174437`、
  鑽頭 `6→5`、confirmation=`baseline_changed`，CNN 未參與成功判定。
- [x] 合併 `main` 後以 7fe98fc6 CDP 9226 正式 live 驗證；完整相關測試更新為 123 passed。

---

## 🚧 2026-07-17 A 打 / B 算：競技場 + 萬神試煉（免洗帳號當計算機）

### 背景與已驗證事實

- 架構文件：`docs/protocol/BATTLE_SIM_ARCHITECTURE.md`
- 命名（本專案定案）：**A = 實戰帳號**（start + 回 result）；**B = 計算機**（任意已載入同網址 H5，免洗號即可）
- 已 live 驗證：
  - 競技場 Arena：`ChapterType=5`，`chapterId=50001`，`arena_combat` → `BattleMainServer` → `{vid,wid}`（`tools/arena_battle_sim.py`）
  - 萬神 rogue：`ChapterType=37`，`chapterId=50001`，`rogue_main_combat` → `BattleMainServer` → `{result,precent}`（小寶 CDP 9226，2026-07-17：5 次重算全同且對齊官方 result、server `is_win=1`）
- 官方 client **本來就先 headless 秒算再播動畫**；A/B 是把「算」拆到 B 帳，A 可跳過長動畫等待
- **不可**只傳 role id；必須傳 start s2c 整包（seed + atk/def 完整單位資料）
- B 不算完 → A **禁止**瞎報 winner（server 會驗算）
- 地獄之門不進本池（走 timeScale）

### 目標

為 **競技場戰鬥**、**萬神試煉** 各加一條可選 A/B 路徑：A 本帳開打，B 用「相同 `mushroomh5` 網址的免洗帳號」算勝負，A 回報 result；失敗可 fallback 本機/原動畫路徑。

### 設計定案（實作前鎖定，有異議再開討論）

| 項目 | 定案 |
|------|------|
| 角色 | A=實戰 device；B=固定免洗 H5（可 headless） |
| B 連線 | 本機常駐 CDP port（config：`battle_calc.cdp_port` / `web_debug_port`） |
| 傳輸 | 本機 HTTP 小服務（A → POST payload → B 回 result）；同機 queue 亦可，首版用 HTTP 好測 |
| 序列化 | payload JSON：**需可跨頁還原**（protobuf 物件不能直接丟；在 A 端 hook 時抽可填 `BattleDataFill` 的欄位，或在 B 端用同一頁的 structured clone 協議） |
| 模式表 | `arena` / `rogue`（萬神 main combat）；切磋可後加 |
| 開關 | per-device `battle_calc.enabled` + 模式白名單；**dashboard 必有開關**（專案鐵則） |
| fallback | B 逾時/掛掉 → A 本機 `BattleMainServer`（H5）或維持現有點擊等動畫路徑 |
| 不做 | 純 Python 重寫引擎；地獄之門；偽造未經驗算的 result；ADB 當 B（無 JS 引擎） |

### Checklist

#### 0. Spec / 文件
- [ ] `docs/superpowers/specs/2026-07-17-battle-calc-ab-design.md`：A/B 契約、payload schema、逾時、fallback、設定鍵、不做清單
- [ ] 更新 `docs/protocol/BATTLE_SIM_ARCHITECTURE.md`：補萬神 live 驗證列；「下次規劃」改為進行中並對齊 A 打 / B 算命名
- [ ] 補 rogue 一列到「已驗證模式」表（ChapterType 37 / result 欄位 / 小寶 9226 日期）

#### 1. 共用核心：`battle_calc/`（B 端 + 協定）
- [ ] `battle_calc/schema.py`：`BattleCalcRequest` / `BattleCalcResponse`（mode, seed, chapterType, chapterId, atk/def blob, vid?, 回傳 result/wid/precent + ms + error）
- [ ] `battle_calc/modes.py`：mode 註冊表
  - `arena`: ch=5, id=50001, fill 第三參 `Arena`, 輸出 `{vid, wid}`
  - `rogue`: ch=37, id=50001, fill 第三參 `Rogue`（**必傳**，否則 `configChapter_type` null → pve crash）, 輸出 `{result, precent}`；`atk_data[0]`/`def_data[0]`
- [ ] `battle_calc/b_runtime.py`：連 B 的 CDP → 預載 `BattleMainServer`/`BattleDataFill` → `simulate(payload)` → 回結果；支援 N 次 deterministic 自檢
- [ ] `battle_calc/server.py`：本機 HTTP（例 `127.0.0.1:18765`）`POST /v1/simulate` + `GET /health`；單 worker 佇列避免 B 頁 JS 併發踩踏
- [ ] `battle_calc/client.py`：A 側 `simulate(mode, payload, timeout=…)` → HTTP；失敗 raise 明確錯誤
- [ ] 單元測試：schema round-trip；modes 參數表；client mock server；server 拒未知 mode

#### 2. B 帳號（免洗計算機）落地
- [ ] config：`global.battle_calc`（或 host_settings 覆寫）
  - `enabled` / `base_url` / `cdp_port` / `web_url`（預設同 `mushroomh5.acenetgame.com`）
  - `profile_dir`（獨立 playwright profile，免洗號登入態）
  - `headless` 預設 true
  - `timeout_ms` / `fallback`: `local_sim` | `animation` | `abort`
- [ ] 啟動：master 或獨立小行程 `python -m battle_calc.worker` 掛起 B 瀏覽器 + HTTP
- [ ] 文件：如何用免洗號登入一次、之後 headless 複用 state
- [ ] health：B 未登入/無 `netManager`/`System.import` 失敗時 `/health` 報 not_ready

#### 3. 競技場 A 路徑
- [ ] 釐清現有 `click_arena_challenges`（OCR 點 3 次）→ 可注入點：收到 `arena_combat_s2c` 後攔截官方自動 result，改走 B
- [ ] H5 路徑優先：hook `arena.arena_combat_s2c` → 組 payload → `battle_calc.client` → `arena_result_c2s {vid, wid}` → 關結算 UI
- [ ] 需抑制/搶在官方 `PvpControl.on_arena_combat_s2c` 送 result 之前（或讓官方算完但跳過動畫；首版以「攔截 send 或提前 result + 跳過 BattleHub」為驗收）
- [ ] fallback：B 失敗 → 本機 CDP 同頁 `BattleMainServer`（A 自己當算）→ 仍失敗才走原動畫
- [ ] 測試：mock combat payload → 回 wid 契約；enabled=false 不碰原流程
- [ ] live：小寶或 5554 開 A/B，打 1 場競技場，對齊 server `is_win`

#### 4. 萬神試煉 A 路徑
- [ ] `battle/rogue_h5.py` / `weekly_trials.py`：每關 `開始挑戰` 後
  - hook `rogue.rogue_main_combat_s2c` → B 算 → 送 `rogue_main_result_c2s {result, precent}`
  - 跳過/縮短等結果窗與「跳過」動畫輪詢（仍要關 `RogueBattleResultView`）
- [ ] 注意：官方 `RogueControl` **同步**秒算後立刻 `send result`；攔截策略與競技場共用「A 端 result 閘道」
- [ ] ADB 萬神：無 JS → 不能 A 本機 sim；僅當 A 是 web_h5 或 A 另開 H5 session 時啟用；否則維持 OCR 路徑
- [ ] 測試：rogue payload fixture → result/precent；until_cap/局數邏輯不變
- [ ] live：小寶萬神 1 關 A/B，5 次 seed 重放 deterministic + server 接受

#### 5. 設定 / Dashboard
- [ ] `config_manager` 預設 + clamp + host_settings 覆寫
- [ ] dashboard：裝置卡或全域「戰鬥遠端計算」開關（啟用/停用、B 健康狀態、fallback 顯示）
- [ ] 僅 web_h5 或「本機可 CDP」裝置顯示可用；B not_ready 時灰掉並提示

#### 6. 觀測與安全
- [ ] log：`logs/battle_calc.log` — mode/seed/ms/result/fallback 原因（**不寫 cookie**）
- [ ] payload 落檔可選 debug（`logs/battle_calc/payloads/`，預設關）
- [ ] 逾時：B > timeout → fallback；連續 N 次失敗 → 本輪 disable A/B 並告警
- [ ] 確認 B 免洗號與 A **不同 role**，避免同帳互踢

#### 7. 驗收
- [ ] 同 seed：B 算 == A 本機算 == 官方 result
- [ ] 競技場 3 場、萬神 3 關：server code=0，進度正常
- [ ] B 進程 kill：自動 fallback，任務不卡死
- [ ] 開關 off：行為與現網完全一致
- [ ] 目標測試（勿裸 pytest）：`tests/test_battle_calc_*.py` + 相關 arena/wanshen 回歸
- [ ] runtime 改動後提醒重啟 `new_main_v2.py` / battle_calc worker

### 建議實作順序

1. Spec + schema + B runtime + HTTP（可先用現有小寶當 A、另開免洗 B）
2. 競技場 A 接線（流程短、既有 sim 工具完整）
3. 萬神 A 接線（局內多關、結果窗/結算 UI 較多）
4. Dashboard + fallback 硬化 + live 驗收

### 風險

- **攔截官方 result 時序**：listener 順序；可能要 wrap `netManager.send` 擋重複 result
- **payload 跨頁**：JSON 化後 `BattleDataFill` 欄位是否齊（live 要用 fixture 驗）
- **B 單點**：多 A 併發需 queue；首版單 B 足夠
- **ADB A**：不能本地 sim，只能 B 或維持動畫

---

## 🚧 2026-07-09 工具 WS 斷線根因：在線監控與工具搶帳號（registry TOOL 無法搶佔借用者）

根因（live 事證 logs/system/online_monitor.log + logs/emulator-5554/main.log）：
- 在線監控是「遊牧偵測器」：20:33 連 7fe98fc6 → 20:38:31 連 emulator-5554（**佔用 80 分鐘到 21:58**）→ 23:03 連 5556。
- 監控登入 = 同帳號異地登入 → 把正在用該帳號的工具 WS 踢斷（裝飾升級 [7/42] 斷線）。
- 監控持有 ONLINE_MONITOR lease（優先權 40）> TOOL（20），且 registry 規定「嚴格高於現任才可搶佔」
  → **工具對監控佔用的裝置連 preempt=True 都搶不回**，ensure 一律 conflict
  →「一鍵抽卡」按下去只得到「此帳號目前在線中（online_monitor）」報錯（有報錯沒工作）。
- 設計文件 §1.3/§5.2 本意是「人授權的 TOOL 搶佔是唯一例外」，但 registry 實作沒有這個例外 → 實作落差。

修法（design §1.3 的人授權例外落地；監控端已有 preempted 讓位路徑，不用改）：
- [x] `runtime_services/session_registry.py`：acquire 搶佔規則加例外——
      `owner is TOOL and preempt=True` 可搶佔**借用型** owner（MONITOR/CHECK/TRACKER）；
      SCHEDULER 仍不可被 TOOL 搶（bot 主迴圈保護）。
- [x] `control_panel/ws_session.py` `ensure()`：TOOL conflict 且現任是借用型 → 自動以
      preempt=True 重試一次（借用者本來就該讓位給人的手動操作；監控會自動換一台）。
- [x] 觀測性：session_registry acquire/release/conflict + ws_session 事件落檔
      `logs/system/session_registry.log`（registry+ws_session logger 共用 rotating
      handler；pytest 不掛）。
- [x] 測試：TOOL preempt 三種借用者成功且觸發 preempted、TOOL 不可搶 SCHEDULER、
      無 preempt 仍 conflict、ensure 自動搶佔、借用型 owner 不自動搶佔（158 related pass）。
- [ ] 生效需重啟 `new_main_v2.py`（未重啟，跑中 bot 仍是舊行為）。

同日已修（同一事件鏈）：裝飾升級步間隔自適應（31f58e97）、一鍵抽卡 job 斷線重連 +
抽數依 bundle 計（150c76fa）、TOOL 搶佔根修（575abcaa）。

---

## 🚧 2026-07-07 暫停按鈕無法中斷 WS + WS 後 H5 不該開瀏覽器

根因（systematic-debugging Phase 1-3 已完成）：暫停用 per-device `threading.Event`
(`bot_state._pause_events`)。`check_pause(ip)` 會 block 到恢復，但只在迴圈/喚醒邊界呼叫。
WS pipeline（`ws_token/runner.py::run_device`）在每個任務邊界 + 開燈/挖礦內迴圈輪詢
`should_abort()`，而兩個 `_should_abort` 都只查 `has_pending_force_sleep`（ws_phase 另加
web_launch），**都沒查暫停** → 強制休眠能中斷，暫停不能。且主迴圈在 WS 階段與喚醒瀏覽器
之間沒有 check_pause → 「開了瀏覽器才暫停」而非「暫停就不開」。與 line 27「force-sleep
中斷」是同構問題（該次只補了 force-sleep，漏了 pause）。

修法：新增非阻塞 `is_paused()`，讓 pipeline 暫停即 abort，並在「開瀏覽器前」block。
- [ ] `bot_state.py`：加 `is_paused(ip)`（peek：pause Event 存在且未 set）。
- [ ] `game_actions/ws_phase.py` `_should_abort`：force_sleep 後加 `if is_paused(ip): return True`。
- [ ] `runtime_services/ws_runner_service.py`：`_should_abort` 加 `or is_paused(ip)`；
      except ForceSleepRequested 後、run_sleep_cycle 前 `if is_paused and not force_sleep_now: continue`。
- [ ] `runtime_services/web_session_service.py` `initialize_runtime_device`：
      `before_web_device_start()` 後、`create_web_device_if_enabled` 前加 while 暫停閘
      （block；手動開網頁優先 break；恢復後重跑 WS 續做）。
- [ ] `new_main_v2.py`：快取條件加 `and not is_paused(ip)`（半套 WS 不快取）；主迴圈 WS 後、
      喚醒前 `if is_paused and not has_pending_force_sleep: check_pause; continue`。
- [ ] `tests/test_bot_state_pause.py`：`is_paused` 反映 set_pause；未註冊回 False。
- [ ] 生效需重啟 `new_main_v2.py`（無 hot-reload）。

涵蓋：離線 WS fallback 由上述 `_should_abort` + sleep 內既有 check_pause 涵蓋；
`dungeon_scheduler` should_stop 已含 check_pause。

---

## 🚧 2026-07-06 移除 5554/5556 喚醒「等 5 分鐘」硬編分流（已 merge main）

背景：dashboard 顯示 5556「正在檢查螢幕狀態」5 分鐘卡住，使用者困惑。查證：
- 空等在 `wake_up_handler.py`（原 348-359），只對 5554/5556，夾在 WS 階段（`new_main_v2.py:304`，
  已在喚醒當下跑完）之後、瀏覽器那半段之前 → 只延後瀏覽器、沒錯開 WS，還把客戶端工作推出 :00–:20 窗。
- 正解機制早已存在：`wake_minute_offset`（`sleep_service.py:118-130`，窗內固定分鐘分流，保留窗）。
- 現況 offset：5554=0、5558=5、5556=15；parity：5554/5556/5558=even、5560=odd。
  5554/5556 已差 15 分醒來、5554 vs 5560 分屬偶奇小時永不撞。
- 反證：舊空等把 5554 從 :00 推到 :05，**反而撞上 5558 的 :05** → 刪掉後分佈更好。

- [x] 刪除 `utils/wake_up_handler.py` 5554/5556 5 分鐘 while 迴圈；保留 `3a8d31f2` 的 10s
- [x] `py_compile` 通過；`check_skip_sleep` 本檔僅此處用到，移除後 `bot_state` 仍他處使用
- [x] worktree `remove-wake-stagger-wait`（29b686de）→ 套回 main（wake_up_handler 兩邊相同直接取檔）
- [ ] 驗證：5554/5556 醒來後直接跑完 WS+瀏覽器、dashboard 不再卡「檢查螢幕狀態」（需重啟後實測）
- [ ] 生效需重啟 `new_main_v2.py`（無 hot-reload）

---

## 🚧 2026-07-06 強制休眠必須立即中斷（含 WS 階段）+ 5554/WS 搶登入排查

背景：dashboard 按「強制休眠」沒反應。根因＝WS 執行路徑沒有 force-sleep 中斷點：
- `ws_runner_service.run_ws_device_cycle` 呼叫 `run_device` 完全沒傳 `should_abort`（純 WS 裝置整輪跑完才理信號）
- `ws_phase._should_abort` 只輪詢「開啟瀏覽器」請求（且僅 web_h5），不看 FORCE_SLEEP
- Playwright 路徑本來就有 `_pause_guard`（每個裝置動作前 raise），只有 WS 是死角

計畫（worktree 隔離）：
- [x] `bot_state.py`：加 `has_pending_force_sleep(ip)` 非消費 peek
- [x] `game_actions/ws_phase.py` `_should_abort`：force_sleep pending 也回 True（不限 web_h5）
- [x] `runtime_services/ws_runner_service.py` `run_ws_device_cycle`：傳 `should_abort` 進 `run_device`，返回後消費信號 raise ForceSleepRequested
- [x] `new_main_v2.py` `_run_ws_phase_for_wake`：WS 階段後消費信號 raise（init/主迴圈/備援三呼叫端統一咽喉點）
- [x] 測試（tests/test_force_sleep_ws_interrupt.py，6 案例）+ 相關 122 測試全綠 → merge main（f37abb70）→ worktree/branch 已清
- [ ] **待辦：重啟 `new_main_v2.py` 生效**（無 hot-reload）
- [x] 並行排查「5554 搶登入」完成 → 根因＝三服務（mount-tracker/online_monitor/online_check）各自 advisory 檢查、零協調、共用同池帳號；詳見下一節設計案

## 🚧 2026-07-06 帳號佔用 registry + bot_state 兩態重構（設計中，待使用者核准）

使用者已決策：① 在線監控走白名單+顯示借用 ② bot_state 底層一併重構兩態（上線/離線 + owner + 通道 ws/h5/adb）③ 特殊喚醒收斂單點保留。
登入權限：白名單（腳本排程/坐騎追蹤/在線監控）免確認；其他 dashboard 分頁切裝置不自動登入、手動點登入、在線中彈確認 modal。
- [x] 設計文件：`docs/superpowers/specs/2026-07-06-account-session-registry-design.md`
- [x] 使用者核准：**一次做完 Phase 1-4**（Phase 5-7 之後再議）
- [x] Phase 1：`runtime_services/session_registry.py` + 單測（a2a70da4）
- [x] Phase 2：ws_session 改走 registry，is_active 消盲區、pause 移交（a7723070）
- [x] Phase 3：mount-tracker 借用改 acquire + check_wake 原子閘門（修誤判可借+TOCTOU，18568078）
- [x] Phase 4：online_monitor/online_check 改走 registry（5558 一般路徑硬擋、三服務不撞台，29036d7d）
- [x] Opus review：無 blocker；三項修補已落地（monitor loop try/finally 防 lease 洩漏、工具路由透出 conflict 訊息、protected 空集不快取，c44b2121）→ merge main（ed8de0de），211 相關測試全綠
- [ ] **待辦：重啟 `new_main_v2.py` + control panel 生效**（連同上一節強制休眠修正一起生效）
- [ ] 重啟後 live 驗證：當前 detector 裝置「開啟瀏覽器 + 手動立即喚醒」即時性（monitor 借用現在會 pause 該台，讓位靠 120s handoff，穩健版等 Phase 5）
- [ ] Phase 5-7 待議：喚醒 SCHEDULER preempt、統一喚醒計算器、bot_state 兩態 + dashboard UI（登入按鈕/確認 modal/借用顯示）；TOOL 人工確認搶佔需 registry 加 force 機制（Phase 7）

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
- `p_dragon_realm_event = {id:u64 #1, event_id:u32 #2, role_id:u64 #3, status:u32 #4, data:repeated #5}`；協助可用 `status!=1`，我方可領獎為 `status==1`
- `provide_help_c2s = 0x4F14 (20244) = {help_target:u64 #1, event_id:u32 #2}`；fire-and-forget，重讀清單確認
- `receive_help_event_c2s = 0x4F17 (20247) = {event_id:u32 #1}`；只送我方 `status==1`，重讀清單確認
- 重用 `control_panel/ws_session`（ensure→get_client→disconnect；內建暫停 bot/踢線/sweeper）
- `codec.walk()` 保留 repeated（多事件）；`walk_dict` last-wins 只能解單欄

- [x] TDD `tests/test_dragon_sos.py`：rescue_pending（fake client，2 事件1pending→只對 pending 送 0x4F14、重讀回報）；is_dragon_open（注入時間）— 11 測過
- [x] `ws_token/dragon_sos.py`：`read_help_list` / `rescue_pending(client)` / `rescue_via_ws(ip, session=ws_session)`
- [x] `game_actions/dragon_realm_scheduler.py`：加公開 `is_dragon_open(now=None)`（純加，不動現有）
- [x] `control_panel/routes_dragon_sos.py`：`POST /api/dragon_sos/<ip>` + `GET /api/dragon_sos/status`；註冊進 control_panel_app
- [x] `templates/dashboard.html`：每 web_h5 列加 SOS 鈕（status.open 才顯示）+ 點擊 POST + toast
- [x] `read_help_list` 對真實 0x4F15 s2c 驗證（help_hp=2 正確、空清單不 crash）
- [x] 純 WS + H5 自動領取我方協助獎勵：`status==1`、role_id 過濾、0x4F17 領取、重讀確認；相關測試與設計規格已補
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

## Review — 2026-07-10 在線標示五態 + 工具頁手動連線 (merge 5bb3f9a2)
- 完成：/api/status lease 注入 + precheck 端點、dashboard 徽章五態、倉庫/工具/飛寵手動連線 + 連線前確認 modal、喚醒 SCHEDULER lease（借用者搶回/TOOL 等待/入睡釋放）。59 tests pass。
- Deferred minors（final review）：
  - 裝置 thread 崩潰（未經 run_sleep_cycle）會殘留 SCHEDULER lease，工具連線被拒直到 thread 重生入睡；低機率，之後可考慮 TTL。
  - acquire_scheduler_lease 在 borrower→TOOL 瞬間切換的微觀 race 下可能 preempt 到 TOOL；視窗極小，接受。
  - session_registry.py:122 註解仍提舊函式 wait_for_dashboard_ws_release（該檔本批禁改）。
  - ws_session TOOL label 目前固定「工具」，之後可讓各工具頁傳有意義 label（倉庫/裝飾升級）。
- 需重啟 new_main_v2.py 生效（Phase 5 + control panel）。

## Plan — 2026-07-11 final_v1 重設計：模擬測試對齊實機 (background session)

> 起因：final_v1 驗收 gate 失敗（e8c67f47），但量測口徑與實機不符：
> sim iteration 對 v1 = 整份 plan 爆發執行、對 final_v1 = 單步；time-cap 截斷成 38/26 局
> 卻用總和統計；實機 WS 其實所有 planner 都是每步重 plan。

- [x] T1 harness 對齊實機（前 session d7926707 已做 exec_mode step/plan + 鎬子歸零終止；
      本 session 609b0a87 補 run_paired seed 配對 + 每局平均統計）
- [x] T2 final_v1 重設計（前 session 67c75718/c0f13949 位能場+調權重；本 session 70c8e834 多步輸出）
- [x] T3 驗收完成並 merge main（99c6afdf）：ADB 等鎬池 score +11%、WS 21列 +2.4% 且鏟耗 -49%、
      replay 2618 面 max 172ms 全達標；WS 7列 -3.6%。預設維持 v1，final_v1 建議只給 WS 裝置 opt-in。
      詳見 docs/superpowers/plans/2026-07-11-final-v1-mining-planner.md 尾段。

## 2026-07-12 final_v1 效率大改 + 地圖回放（orchestration plan）

主 session（Fable）只做方向/規劃/審查；實作全派 Opus subagents。

### Stream A：final_v1 綜合效率（pit/eq 與 pits 同時贏 v1）— 完成
- [x] 提案 A（產出分級道具成本 + parity tie-break）— 30 局快掃**不過**（step 口徑 pits 崩跌 49.3 vs 64.0），淘汰
- [x] 收 codex 設計討論 + ITEM_LOW_YIELD_COST 響應曲線 + 7/21 列視野診斷
- [x] 定案機制：真實 3eq 計價 + KPI action_cost + branch 配額 + cluster 身分保存 + profile 影子價(step3.6/plan3.0)
- [x] Opus 實作 + 掃描，離線驗收：WS21 pits +22%/pit·eq 0.205、ADB pits +141%/0.200，bootstrap CI 下界 >0，延遲達標
- [x] 5556/5560 CDP 實機驗證：CNN 辨識 vs 畫面一致；動態閉環 final_v1 決策→遊戲WS執行→盤面精準變化+礦坑導向實證

### Stream B：每帳號挖礦地圖完整記錄 + 回放（Task #7）— 完成
- [x] mini-spec（JSONL 格式/路徑/rotation/dashboard 開關）
- [x] Opus 在獨立 worktree（自 67a7f611 分支）實作 recorder + CLI 回放 + 開關，測試綠
- [x] 主 session 審 diff + 複跑測試 → 待 merge

---

## 跨界停車抱團掃描調整 (2026-07-12)

需求（使用者確認）：不再綁定車位9；掃全 1-30；手機fc(adb-fc65396d)+小寶(7fe98fc6) 優先 1-15，
三台模擬器(5554/5556/5560) 優先 15-30；每5秒一輪、上限5分鐘；找到 ≥2 台同服即停；停到就停掃。

- [x] carpark_plan.ClusterScanConfig 加 `priority_levels`；`parse_cluster_scan` 解析（含空預設）
- [x] carpark.scan_lots_same_server 加 `priority_levels`：排序 priority-range 優先（rank0），組內 count desc / ceng asc
- [x] runner._run_carpark：cluster_scan gate 從「僅 09:59 grab」放寬為「任一開窗內停車皆掃」；
      傳 priority_levels；改挑「第一個達 min_allies」的 lot（非只看 ranked[0]）；
      timeout fallback prefer_levels 用 priority range（不再綁車位9）
- [x] bot_config.json：5 台啟用裝置設 cluster_scan（min_allies=2、levels 1-30、priority 依組、dur300/int5），
      silver_levels 同步改成 priority range（避免 cluster_server_id 缺席時仍綁 9/10）
- [x] tests/test_carpark_cluster_scan.py：priority 排序 + parse priority_levels（11 passed）
- [ ] **DEFER（待 recon）**：使用者要「停 30 分鐘後複查，若該車位同服車數(含我)≤5 就移動到能抱團且有空位的車位」。
      協議 dump 沒有「提前收回/移動已停跨界車」的指令（CARPARK_PROTO_SCHEMA 只有 park，無 unpark/move）。
      需 live 抓封包確認遊戲能否在 8h auto-collect 前提前收回並重停。確認可行後再實作 30 分複查 + 移車。

備註：無 hot-reload — 改了 ws_token/runner.py 等模組後，正在跑的 bot 需重啟 new_main_v2.py 才生效。

## Dashboard 加載變慢 — /api/status OCR health 探測卡 2 秒 (2026-07-12)

根因：主 OCR server `100.64.0.5:5001` 掛掉，`check_ocr_server()` 每次同步逐台探測
（timeout=2s、死的排第一），dashboard 每 2 秒輪詢 /api/status → 每次輪詢都卡 2s+。
備援 100.64.0.7 實測 3ms 就回。

- [x] `check_ocr_server` 加 30s TTL 快取（`_OCR_HEALTH_CACHE`）
- [x] 記住上次成功 server（`_OCR_LAST_GOOD`）優先探測；health timeout 2s → 0.5s
- [x] tests/test_ocr_health_cache.py（3 tests）+ test_presence_lease_fields 迴歸，8 passed
- [x] merge 到 main（849539f1），worktree/branch 已清（目錄殼被 NAS 同步佔住，可稍後手刪）

備註：無 hot-reload — 正在跑的 control panel 要重啟 new_main_v2.py 才吃到新程式碼。

## 🚧 2026-07-18 在線檢查納入寶兒/暴走（真人帳號在線偵測）

### 目標
讓「在線檢查」能檢查寶兒(web-001)/暴走(web-002)——兩者都要：
- **閘門式**：bot 開瀏覽器前先確認真人在不在線，在線就不啟動（不踢真人）。
- **監控式**：dashboard 能看到這兩個真人帳號是否在線。

### 已驗證事實（2026-07-18，5554 純讀取擷取，零操作）
- 寶兒 = `寶兒࿐` roleId **89562953024526**，家族「ღ雪夜城༄」；**是 5554 好友**（friend-list 可解）。
- 暴走 = `꧁爆走天使꧂` roleId **89559731802158**，家族「羽皇居」guild_id **89538256961538**（15級/81人）；**非 5554 好友**，但 guild-member 查詢已驗證可解（實測 is_online=True，真人在玩）。
- 機制：`check_via_ws`（`ws_online_checker.py`）先 `friend_presence`（好友列表），None 再 `is_role_online_in_guild`（家族成員 is_online）。guild_id 來源 = checker 的 `online_check_guild_id`（`online_check_service._guild_for`）。
- requester 閘門：裝置設 `online_check_target_pid` → `web_session_service` 走 checker gate，真人在線就擋 web 啟動。
- monitor 快照只讀好友列表（`OnlineMonitor.poll_friends`）→ 寶兒(好友)會進快照，暴走(非好友)不會。

### Phase 1（純改 config，零程式碼，低風險）— 閘門式 ✅ 2026-07-19 完成
- [x] web-001 加 `online_check_target_pid: 89562953024526`
- [x] web-002 加 `online_check_target_pid: 89559731802158`
- [x] checker 裝置（5554/5556/5560）加 `online_check_guild_id: 89538256961538`（羽皇居）→ 解暴走；寶兒由 5554 好友列表解
- [x] 驗證（`tools/verify_online_check_ws.py`，checker=5554）：暴走 89559731802158 → **True ONLINE**（guild fallback）；寶兒 89562953024526 → **False OFFLINE**（friend）。兩路徑皆通。
- 註：寶兒跨家族(雪夜城)，目前只靠 5554 好友解（使用者選不硬化）；解不到時保守回 None＝不放行，安全。
- ⚠ 無 hot-reload：正在跑的 bot 需重啟 new_main_v2.py 才吃到新 config。

### Phase 2（小改程式碼）— 監控式 dashboard 顯示
- [ ] `OnlineMonitor` 增設「額外家族目標」輪詢：對設定的 (guild_id, role_id) 每輪讀 guild members，把命中的成員以 StatusEntry(online→last_login_ts=0) 併入快照
- [ ] 暴走(羽皇居)注入快照 → dashboard 顯示 + gate fast-path(`_check_monitor_snapshot`) 也能命中，省掉每次 WS 登入；寶兒已在好友快照內
- [ ] 加測試（AAA，不連真裝置）

### 前置需求 / 風險
- 無 hot-reload：改 config 後正在跑的 bot 要重啟 new_main_v2.py 才生效。
- 嚴禁登入寶兒/暴走本身（真人使用中）；所有偵測走 checker 帳號的好友/家族查詢。

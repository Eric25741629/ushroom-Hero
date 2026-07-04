# 菇勇者全自動掛機：遊戲自動化優化建議

> 目標：把現有「能跑」的自動化腳本，整理成「比較穩、比較好改、比較不容易互相干擾」的系統。

## 1. 分析範圍

我先盤點了專案結構與主要程式碼，以下為本次分析的主要觀察範圍：

- 根目錄腳本：`fight_car.py`、`new_battle.py`、`daily_gift_task.py`、`red_envelope.py`、`BUY.py`、`Sea.py`、`Mission.py`、`Skill.py`、`Spin_Wheel.py`、`gold_mananer.py`、`oralce_manger.py`、`spin_and_send_gold_single_runner.py`
- 子系統：`battle/`、`farm_v2/`、`sea_v2/`、`miner/`
- 共用基礎設施：`img_tools.py`、`tools.py`、`mask.py`、`game_state/detector.py`、`game_actions/navigation.py`、`json_manager/`

## 2. 目錄結構觀察

### battle/
```
battle/
  __init__.py
  _helpers.py
  biweekly.py
  cloud.py
  manager.py
  special.py
  store.py
  weekly_trials.py
  __pycache__/
```

### farm_v2/
```
farm_v2/
  __init__.py
  config.py
  manager.py
  # states.py（FarmState/FarmContext 狀態機）已於 2026-07-05 移除：未接線、死碼
  operations/
    __init__.py
    base.py
    plant.py
    seed.py
    weekly_card.py
  __pycache__/
```

### sea_v2/
```
sea_v2/
  __init__.py
  map_cache.py
  navigator.py
  session.py
  shared_map.json
  tasks.py
  tiles.py
  __pycache__/
```

### miner/
```
miner/
  __init__.py
  ai_tuner.py
  algo_evolver.py
  auto_optimizer.py
  mining_service.py
  simulator_bridge.py
  core/
  dataset/
  models/
  planning/
  rl/
  rl_logs/
  scripts/
  v2/
  v3/
  v4/
  __pycache__/
```

### everyday_mission/
```
everyday_mission/
  __init__.py
  Guardian_Spirit_manger.py
  __pycache__/
```

### mission / reward_get / partner
這三個資料夾在專案根目錄目前看來是空的或只剩空結構，主流程實際上沒有明顯依賴它們的內容。

---

## 3. 最值得先改的 10 件事

下面不是全面重寫清單，而是「投入產出比最高」的改進優先序。

### 3.1 把「像素魔法數字」收斂到設定層
**問題**
- `Mission.py:68`、`battle/manager.py:58`、`tools.py:52`、`mask.py:5` 等地方，大量出現：
  - 固定像素座標
  - 固定顏色 `np.sum([...]) <= 10`
  - 固定等待秒數 `time.sleep(5)`
- 這些數字一旦遊戲版面調整，就會同時壞很多地方。

**建議**
- 先建立一層「畫面設定檔」：
  - `screen_profile.py`
  - `coords/`
  - `colors/`
  - `waits/`
- 把當前硬編碼值抽成具名設定，例如：
  - `HOME_BTN = (321, 920)`
  - `CONFIRM_COLOR_TOLERANCE = 10`
  - `ANIMATION_SETTLE_SHORT = 0.5`
- 優先抽 `game_state/detector.py`、`Mission.py`、`tools.py`、`battle/manager.py`、`Sea.py`，因為它們重複最多、影響最廣。

---

### 3.2 建立統一「畫面偵測」與「主頁確認」服務
**問題**
- `Mission.py:61-73`、`tools.py:46-54`、`game_state/detector.py:8` 都各自用像素顏色判斷主頁。
- `capture_screenshot()` 也出現在多處，且邏輯長得不完全一樣。

**建議**
- 抽成單一共用服務：
  - `HomePageVerifier.is_main_page(img)`
  - `ScreenState.capture(device)`
  - `StageResolver.resolve(device, img)`
- 之後所有任務只問：
  - `stage = stage_service.resolve(d)`
  - 不再各自有一套 color check。

---

### 3.3 把重複的「每日任務是否已做」邏輯統一
**問題**
- `Spin_Wheel.py:46`、`daily_gift_task.py:104`、`battle/special.py:73`、`json_manager/` 各自有不同方式檢查：
  - 同一天
  - 同一週
  - 是否超過次數
- 有些用 `StoreDataManager`，有些用 `TimeRecordDataManager`，有些直接讀 timestamp。

**建議**
- 統一成一組任務排程 API：
  - `schedule.should_run(task_key, policy="daily" | "weekly" | "cooldown" | "count_limit")`
  - `schedule.mark_done(task_key, metadata={})`
- 把 `json_manager/scheduling.py` 升級為任務狀態單一事實來源。

---

### 3.4 把「轉盤 / 金幣 / 紅包 / 每日贈禮」做成獨立 daily task modules
**問題**
- `Spin_Wheel.py`、`daily_gift_task.py`、`red_envelope.py` 都混著：
  - 畫面檢查
  - 點擊流程
  - 每日排程判斷
  - JSON 狀態維護

**建議**
- 每個日常功能拆成：
  - `task_meta`: 任務名稱、頻率、前置條件
  - `can_run()`: 是否該執行
  - `run_once(device)`: 單次 UI 操作
  - `mark_done(result)`: 回寫排程狀態
- 讓 `daily_pipeline.py` 只負責排序、例外處理、與 stage guard，不再負責每種任務的內部判斷。

---

### 3.5 把 `farm_v2` 的狀態機延伸成專案通用模式
> ⚠ 2026-07-05 更新：`farm_v2/states.py`（`FarmState`/`FarmContext`）從未接線，已隨死碼清理移除。
> 以下屬歷史提案；若日後要做通用狀態機，需重新設計範本，勿再引用已刪的 farm_v2 狀態機。

**現狀（已過時）**
- 曾以 `farm_v2/states.py` 為最接近正規狀態機的設計（`FarmState` + `FarmContext`），但實際未被 `manager.py` 使用，已移除。

**建議**
- 以 `farm_v2` 為範本，建立通用狀態機基底：
  - `TaskState`
  - `TaskContext`
  - `TransitionGuard`
  - `RecoveryPolicy`
- 先導入：
  - 農場
  - 航海
  - 挖礦
  - 副本 / 日任

---

### 3.6 把 `battle/manager.py` 的分支式副本邏輯改成表驅動
**問題**
- `battle/manager.py:89-193` 用大量 `if battle_name == ...` 控制流程。
- 不同副本邏輯混在同一個 `handle_battle()` 裡，之後擴充會越來越難改。

**建議**
- 建立副本任務註冊表：
  - `BattleTaskDef(name, entry_flow, fight_flow, exit_flow, verify_state)`
- 讓 `BattleManager` 只負責：
  - 找入口
  - 啟動副本流程
  - 結果回寫
- 各副本邏輯放到獨立模組。

---

### 3.7 為 `sea_v2` 建立明確的「修船前置條件」與 fallback
**現狀**
- `sea_v2/session.py:18-24` 已經明確寫出：
  - 修船需要船在大本營
  - 港口關閉會離開賽季
  - 有些動作要靠 OCR，有些靠節點
- 這是目前全專案裡「狀態邊界」寫得最清楚的一段。

**建議**
- 繼續保持這個方向，但要把以下條件正式化：
  - `Precondition.is_ship_at_home_base()`
  - `Precondition.is_season_view_active()`
  - `Precondition.is_repair_possible()`
- 並建立：
  - `ActionPlan.execute_with_precondition(action, fallback)`
  - 若前置條件不成立，先自動執行「回大本營」流程。

---

### 3.8 為 `miner` 建立「planner / executor / validator」清晰邊界
**現狀**
- `miner/mining_service.py` 已經有這個方向：
  - classifier
  - planner（v2/v3/v4）
  - executor
  - inventory / board 驗證
- 但主迴圈仍然很厚。

**建議**
- 把服務拆成三層：
  - **Perception**: 截圖、classifier、OCR、board 讀取
  - **Planning**: planner 選版面策略
  - **Execution**: 點擊、道具使用、重試、partial update
- 讓 `mining_service` 變成 orchestrator：
  - `state = perceive()`
  - `plan = plan_next(state)`
  - `result = execute(plan)`
  - `reconcile(result)`

---

### 3.9 把「錯誤回復」變成可重用政策，而不是各模組自己寫死
**問題**
- `battle/_helpers.py:108` 有 `_recover_to_home()`
- `game_actions/navigation.py` 有 `navigate_to_main_page()`
- `game_initialization.py` 有 `resolve_stage_until_stable()`
- 但很多舊模組仍用：
  - `click_white(d)`
  - `d.press("back")`
  - 盲點固定座標

**建議**
- 建立統一 recovery policy：
  - `RecoveryPolicy.recover_to_main(device)`
  - `RecoveryPolicy.dismiss_popup(device)`
  - `RecoveryPolicy.restart_app_if_stuck(device)`
- 任務失敗時只呼叫：
  - `recovery.recover_to_main(d)`
- 不再每個檔案自己拼湊 escape 序列。

---

### 3.10 把根目錄腳本逐步降級為「薄殼入口」
**問題**
- 根目錄有太多「功能完整」的檔案，例如：
  - `BUY.py`
  - `Sea.py`
  - `Mission.py`
  - `Skill.py`
  - `Spin_Wheel.py`
  - `gold_mananer.py`
  - `oralce_manger.py`
- 這些檔案承擔了太多責任，且命名、拼字、層級不一致。

**建議**
- 逐步轉型成：
  - 根目錄只保留入口 / runner / shim
  - 核心邏輯搬進 `game_actions/`、`tasks/`、`modules/`
- 例如：
  - `Sea.py` -> `sea_legacy.py` + `sea_v2/`
  - `Mission.py` -> `tasks/missions/`
  - `Spin_Wheel.py` -> `tasks/daily_spin/`
  - `Skill.py` -> `tasks/skill_partner/`

---

## 4. 依面向的具體優化建議

## 4.1 遊戲自動化邏輯

### 重複代碼
1. **主頁顏色判定重複**
   - `Mission.py:61-73`
   - `tools.py:46-54`
   - `game_state/detector.py:8`
   - 三處都在判「是不是主頁」，且條件幾乎一樣。

2. **capture_screenshot 重複**
   - `Mission.py:54-80`
   - `tools.py:46-58`
   - `battle/manager.py:33-42`
   - 都有「先抓畫面、再判主頁、再重試」的結構。

3. **每日/每週檢查重複**
   - `Spin_Wheel.py:46-91`
   - `daily_gift_task.py:92-140`
   - `battle/special.py:73-125`
   - 都各自檢查「今天做過沒」。

### 硬編碼值
- 座標：`Sea.py:11-57`、`Mission.py:157-210`、`red_envelope.py:9-20`
- 顏色：`mask.py:5-18`、`Mission.py:61-73`、`tools.py:46-54`
- 時間：`daily_gift_task.py:28`、`Sea.py:18`、`Spin_Wheel.py:100`

### 魔法數字
- `Spin_Wheel.py:13`：`RED_BADGE_MIN_PIXELS = 60`
- `battle/manager.py:103`：`time < 5`
- `Mission.py:61`：`abs(...) < 10`
- `miner/mining_service.py:40`：`MAX_EMPTY_PLANS: int = 3`

### 建議
- 建立 `constants/` 或 `configs/` 資料夾。
- 先把 `color`, `coord`, `timeout`, `threshold` 抽成模組化設定。
- 長期目標：同一個「回到主頁」流程，不應在 4 個檔案各有一套座標與等待秒數。

---

## 4.2 狀態機設計

### 現況優點
- ~~`farm_v2/states.py`（`FarmState`/`FarmContext`）~~ 已於 2026-07-05 移除（未接線死碼）；此優點作廢。
- `sea_v2/session.py` 的節點路徑、前置條件、備註都很清楚。
- `game_initialization.py` 有 `resolve_stage_until_stable()`，已經朝共用狀態解析走。

### 狀態管理是否清楚？
- **farm_v2**：清楚
- **sea_v2**：大致清楚，但「修船 / 港口 / 離開賽季」這段邊界仍需更明確
- **battle**：不清楚，主要靠 if/elif 與 OCR 結果硬幹
- **根目錄任務模組**：不清楚，很多是「做完一步算一步」

### 是否有狀態遺漏？
有，至少下面幾組值得補：

1. **登入衝突恢復後的中間態**
   - 目前 `異地登錄 -> app_stop -> 休眠` 有做
   - 但「喚醒後回到哪個 task 繼續」目前幾乎沒有正式狀態承接。

2. **UI 未預期彈窗**
   - 目前只處理了：
     - 公告
     - 放置獎勵
     - 車位倉庫
     - 前往活動
   - 但遊戲常見「活動 / 系統 / 限時彈窗」仍可能卡住 pipeline。

3. **任務中途失敗的回滾態**
   - 很多任務是半完成：
     - 進了副本但沒打完
     - 進了商店但沒買完
     - 進了農場但沒收完
   - 目前沒有標準化的 `partial_failure` 狀態。

### 建議
- 建立全域狀態機層：
  - `DeviceStage`: 主頁 / 子頁面 / 彈窗 / 任務中 / 休眠 / 衝突
  - `TaskStage`: not_started / running / partial_done / done / failed / recoverable
- 每個任務回傳：
  - `TaskResult(stage, recoverable, next_action)`

---

## 4.3 任務調度

### 多任務衝突
目前最明顯的風險有三：

1. **同一輪 pipeline 裡，任務順序影響結果**
   - `game_actions/daily_pipeline.py:37-163` 目前是固定 20 步序列。
   - 例如：
     - 家族任務 stage 會影響後面 guardian / skill partner
     - 地獄之門與農場任務先後會影響主頁穩定性

2. **不同 scheduler 各自檢查時間，但沒有中央節流**
   - `json_manager/scheduling.py`
   - `Spin_Wheel.py`
   - `battle/special.py`
   - 都各自查「今天 / 本週 / cooldown」，但沒有中央節流器。

3. **強制睡眠、異地登入、手動請求會打斷任務鏈**
   - `new_main_v2.py:134-203` 有處理
   - 但任務內部不一定有中斷點保存機制

### 優先級管理
- 目前優先級主要隱含在 pipeline 順序裡。
- 沒有明確的：
  - `priority`
  - `deadline`
  - `precondition`
  - `abort_policy`

### 建議
- 建立任務調度器：
  - `TaskEntry(name, priority, precondition_fn, run_fn, cooldown_policy)`
- 優先級建議分成：
  1. **安全層**：異地登入、帳號保護、強制休眠
  2. **每日層**：限時副本、每日獎勵
  3. **例行層**：農場、挖礦、商店
  4. **可延後層**：紅包、額外收集、UI 優化類任務
- 讓 pipeline 從「固定 20 步」改成「排程器依條件動態選任務」。

---

## 4.4 錯誤恢復

### 異常處理
現況問題：

1. **太多 bare `except Exception`**
   - `daily_gift_task.py:49`
   - `battle/special.py:47`
   - `Spin_Wheel.py:134`
   - `Mission.py:126`
   - 容易吞掉真正錯誤。

2. **很多地方用 `print()` 而不是 logger**
   - `Sea.py`
   - `Mission.py`
   - `Skill.py`
   - `gold_mananer.py`
   - `oralce_manger.py`

3. **失敗後不一定回到已知穩定狀態**
   - 有些函式失敗後只是 `return False`
   - 沒有保證回到主頁或清除 half-open 畫面

### 重試機制
- `battle/_helpers.py:82` 有較好的 `_safe_click_step()`，有 retry 與 timeout。
- 但其它模組多半：
  - 直接 `for i in range(N)`
  - 或 `while time.time() - start < timeout`

### 日誌記錄
- `utils/logging_utils.py` 本身設計不差，有：
  - per-device logger
  - rotating file handler
  - startup log rotation
- 但根目錄舊模組沒有接上這套 logger。

### 建議
1. 建立標準錯誤處理契約：
   - `on_failure = retry | recover_to_main | abort_task | restart_app`
2. 任務統一回傳：
   - `TaskResult(ok, error, retryable, recovery_action)`
3. 全部根目錄舊模組逐步轉成：
   - `logger.info/warning/error`
   - 不再 `print()`
4. retry 策略統一成：
   - `max_retries`
   - `backoff_base`
   - `retry_on_stage_mismatch`
   - `recovery_before_retry`

---

## 4.5 模組間耦合

### 依賴關係現況
- `game_initialization.py`
  - 被 `new_main_v2.py`、`stage_guard.py` 使用
  - 負責啟動、穩定 stage、彈窗處理
- `game_state/detector.py`
  - 被主流程、navigation、pipeline 共用
- `game_actions/navigation.py`
  - 被 farm / main pipeline 使用
- `json_manager/`
  - 被非常多模組依賴，是目前最核心的共用層
- `miner/`
  - 相對最獨立，但服務仍偏厚

### 可抽取的共用邏輯
至少以下四塊值得抽成 shared service：

1. **StageService**
   - `get_stage()`
   - `resolve_until_stable()`
   - `is_main_page()`

2. **RecoveryService**
   - `recover_to_main()`
   - `dismiss_popup()`
   - `restart_app_if_stuck()`

3. **ScheduleService**
   - `should_run(task_key)`
   - `mark_done(task_key)`
   - `get_last_run(task_key)`

4. **ActionRunner**
   - `run_if_main(task_name, fn)`
   - `run_with_recovery(task_name, fn)`
   - `run_with_timeout(task_name, fn, timeout)`

### 高風險耦合
- `Spin_Wheel.py`：同時管 UI、每日檢查、計數、JSON 依賴
- `Mission.py`：同時管截圖、頁面判定、顏色檢查、購買次數
- `Sea.py`：legacy 函式，仍被 pipeline 使用，與 `sea_v2` 形成雙軌
- `Skill.py`：舊式 UI 腳本，缺乏明確狀態與回復機制

---

## 5. 建議的重構順序

### 第一階段：止血
1. 統一 logger，消滅 `print()`
2. 把最常用的 `capture + stage + popup` 判斷抽成共用服務
3. 把重複的「今天是否做過」邏輯收到 `json_manager/scheduling.py`

### 第二階段：穩定
4. 為 `daily_pipeline.py` 建立正式任務抽象
5. 為 `battle/` 建立副本任務表
6. 為 `sea_v2` 補齊前置條件與回復策略

### 第三階段：結構升級
7. 建立通用 state machine 基底
8. 把根目錄舊腳本降級為 shim
9. 讓主循環改為「任務排程器 + 任務模組」架構

---

## 6. 建議的目標架構（精簡版）

```
main.py
  └── scheduler/
        ├── stage_service.py
        ├── recovery_service.py
        ├── schedule_service.py
        └── action_runner.py

tasks/
  ├── daily_spin/
  ├── daily_gift/
  ├── red_envelope/
  ├── battle/
  ├── farm/
  ├── sea/
  ├── mining/
  └── shop/

core/
  ├── device_abstraction.py
  ├── screen.py
  ├── ocr.py
  ├── vision.py
  └── state_machine.py

configs/
  ├── coords/
  ├── colors/
  ├── thresholds/
  └── schedules/

data/
  ├── json_manager/
  └── logs/
```

---

## 7. 結論

這個專案目前最大的問題不是「功能沒有」，而是：

- 同一件事散落在很多檔案裡
- 狀態判斷、重試、回復沒有統一規則
- 新舊系統並存（例如 `Sea.py` vs `sea_v2/`）

因此，最務實的做法不是一次重寫，而是：

1. **先抽共用服務**
2. **再標準化任務契約**
3. **最後才做架構重整**

若照這個順序做，應該能在不中斷現有掛機功能的前提下，逐步提升穩定度與可維護性。

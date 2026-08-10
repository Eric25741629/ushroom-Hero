# 狀態機與註冊表說明意見 v2-gpt

> 文件目的：評估本專案是否應導入狀態機，說明狀態機與任務註冊表各自要解決的問題，
> 並提出可分階段、可驗證、可回退的實施方向。
>
> 這是設計意見，不是已定案規格。本文會刻意把待討論的選擇保留出來，避免在還沒有
> 行為測試與實作數據前，過早把架構固定。

---

## 1. 結論先行

本專案已經出現適合導入狀態機的問題，但不適合把整個系統改寫成一台巨型狀態機。

建議採用兩個互補機制：

1. **每台裝置一個 Runtime State Machine**：只管理裝置執行生命週期，例如初始化、WS 階段、
   喚醒客戶端、客戶端任務、休眠、冷卻、離線與停止。
2. **一個 Task Registry**：只管理「有哪些任務、順序、啟用條件、WS/客戶端實作、
   完成記錄與可否跳過」。

這兩者解決不同問題：

- 狀態機降低的是**控制流與狀態轉移複雜度**。
- 註冊表降低的是**任務知識分散與多後端對照複雜度**。

如果只做狀態機，任務對照仍然分散；如果只做註冊表，休眠、暫停、手動接管、強制休眠與
登入衝突仍然會在大型迴圈內互相穿插。因此本文主張的不是「狀態機或註冊表」，而是
**用最小的狀態機與最窄的註冊表，分別對準兩個痛點**。

---

## 2. 為什麼現在需要討論狀態機

### 2.1 單一主函式已同時負責過多狀態轉移

`new_main_v2.py` 的 `main()` 目前不只是入口，它同時負責：

- 辨識純 WS、ADB 與 `web_h5` 後端。
- 執行啟動延遲與初始化重試。
- 在啟動瀏覽器前執行 WS-first 階段。
- 處理暫停、恢復、手動開網頁、關閉瀏覽器與強制休眠。
- 處理異地登入、連線失敗、離線 WS fallback 與重試冷卻。
- 決定是否略過客戶端、執行每日任務或直接休眠。
- 停止 ADB/Playwright 資源，計算下次喚醒時間並回到下一輪。

以目前程式碼量化，`main()` 約 556 行，含約 60 個 `if`/`while`/`try` 等控制分支。
這不代表「行數大就一定要 FSM」，卻表示任何新增中斷條件都可能同時影響多個進入點、
退出點與 `finally` 清理路徑。

### 2.2 真正的問題是「狀態隱含在控制流中」

現在裝置到底處於哪個生命週期階段，往往不是由一個權威欄位回答，而是必須同時看：

- 目前執行到兩層 `while` 的哪一層。
- `d` 與 `d_orig` 是否已經建立。
- `backend_kind` 是什麼。
- `pre_runtime_ws_done` 是 `None` 還是已完成。
- `resume_sleep_until_ts` 與 `resume_sleep_reason` 是否存在。
- `force_sleep_now`、`skip_phone_cleanup` 等區域變數。
- `bot_state` 中的 pause event、one-shot signal、web launch request 與顯示狀態。

這些資料的單一個都不是錯誤；問題在於它們組合起來才能推斷真正狀態。當組合數增加時，
以下問題會變得很難回答：

- 在 WS 階段收到強制休眠，應由誰消費訊號？
- 暫停期間又收到手動開網頁，哪個優先？
- 手動操作結束後，應恢復原本休眠，還是重跑 WS 階段？
- 登入衝突發生在初始化前、WS fallback 或客戶端運行中，是否都有相同政策？
- 瀏覽器關閉後只要重開瀏覽器，還是必須重建全部 runtime？

狀態機的價值不是少寫 `if`，而是強迫這些問題都有集中、可查詢與可測試的答案。

### 2.3 `bot_state` 已有訊號與狀態，但尚未形成轉移模型

`bot_state.py` 已將部分 one-shot 命令整理成 `Signal` enum，也有每台裝置的顯示狀態、
pause event、WS 登入結果、web launch request 與 online-check request。這些都是可以保留的基礎。

然而，「有狀態資料」不等於「有狀態機」。目前並沒有一層明確定義：

- 哪些轉移是合法的。
- 同時收到多個命令時的優先級。
- 一個事件由誰消費，是否只能消費一次。
- 轉移失敗後狀態應留在原地、進入冷卻還是離線。
- 進入與離開一個階段時必須完成哪些資源動作。

因此本文建議的不是再造一份 `status` 字串，而是在現有訊號與 service 之上建立
「轉移決策的單一入口」。

---

## 3. 本文所說的狀態機是什麼

### 3.1 設計單位：每台裝置一個 runtime

最適合的單位是：

```text
一台裝置 = 一個 DeviceRuntime = 一個 Runtime State Machine 實例
```

不應該以整個 master、整個 worker、每個任務或每個遊戲畫面做為這台核心狀態機的單位。
不同裝置可各自睡眠、手動接管或執行任務，狀態不應互相影響。

### 3.2 狀態粒度：可持續、可中斷的生命週期階段

建議的第一版 `RuntimePhase` 可以很小：

```python
class RuntimePhase(Enum):
    STARTING = auto()
    INITIALIZING = auto()
    WS_PHASE = auto()
    WAKING_CLIENT = auto()
    CLIENT_TASKS = auto()
    SLEEPING = auto()
    COOLDOWN = auto()
    OFFLINE = auto()
    STOPPED = auto()
```

是否應成為 phase，可以用以下問題判斷：

1. 這個階段是否可能持續數秒以上？
2. 這個階段是否可能收到暫停、手動接管或強制休眠？
3. 進入或離開這個階段時，是否需要建立或釋放資源？
4. 是否需要對該階段設定 timeout、重試或恢復政策？
5. 儀表板與 log 是否需要穩定地顯示這個階段？

反之，「點一個按鈕」、「等待兩秒」、「識別主頁」、「第二次重試」都不應成為 runtime phase。

### 3.3 不要把所有「狀態」都塞入同一個 enum

建議將目前混在一起的概念分類：

| 類型 | 用途 | 例子 | 是否為 RuntimePhase |
|---|---|---|---|
| 生命週期階段 | 互斥的主控流 | `WS_PHASE`、`SLEEPING` | 是 |
| 控制模式 | 暫時抑制或接管主流程 | `RUNNING`、`PAUSED`、`MANUAL` | 否，可作為正交欄位 |
| 一次性命令 | 驅動轉移 | `FORCE_SLEEP`、`WEB_CLOSE` | 否，這是 event |
| 畫面觀察 | 描述現在看到什麼 | `HOME`、`ANNOUNCEMENT` | 否，這是 observation |
| 任務進度 | 記錄今日或本輪完成項目 | arena/mining done | 否，這是 ledger |
| 能力與設定 | 決定某路徑是否可用 | `backend=web_h5` | 否，這是 config/capability |

如果把這些概念全部相乘，很快就會出現 `PAUSED_WS_PHASE`、`PAUSED_CLIENT_TASKS`、
`MANUAL_FROM_SLEEPING` 之類的狀態爆炸。正確做法是保留少量互斥 phase，再將控制模式、
事件、觀察與進度分離。

### 3.4 轉移由事件驅動，不由狀態直接呼叫下一個狀態

核心公式是：

```text
目前 phase + event + guard = 下一個 phase + effect intent
```

例如：

| 目前 phase | event | guard | 下一個 phase |
|---|---|---|---|
| `INITIALIZING` | `INIT_SUCCEEDED` | - | `WS_PHASE` |
| `WS_PHASE` | `WS_COMPLETED` | 尚有 client task due | `WAKING_CLIENT` |
| `WS_PHASE` | `WS_COMPLETED` | 無 client task due | `SLEEPING` |
| `WAKING_CLIENT` | `CLIENT_READY` | - | `CLIENT_TASKS` |
| `CLIENT_TASKS` | `TASKS_COMPLETED` | - | `SLEEPING` |
| `SLEEPING` | `WAKE_DUE` | - | `WS_PHASE` 或 `INITIALIZING` |
| 任意允許中斷的 phase | `FORCE_SLEEP` | - | `SLEEPING` |
| 連線相關 phase | `LOGIN_CONFLICT` | - | `COOLDOWN` |

任務實作不可直接寫 `runtime.phase = SLEEPING`。它只回報 `TASKS_COMPLETED` 或其他結果，
由單一的 `dispatch()` 決定轉移。否則狀態機只是多一個名詞，真正的轉移還是散在各處。

---

## 4. 為什麼不能只用任務註冊表

任務註冊表很有價值，但它的責任是「描述任務」，不是「管理 runtime 轉移」。

註冊表適合回答：

- 有哪些任務？
- 任務的穩定 ID、顯示名稱與執行順序是什麼？
- 哪個 config key 控制是否啟用？
- 什麼條件下 due？
- WS、ADB 與 Playwright 哪些後端有實作？
- WS 完成後，哪些 client task 可以略過？
- 完成結果要寫入哪個 ledger/schema？
- timeout、重試與花費政策是什麼？

但註冊表不應回答：

- 在 WS 運行中收到強制休眠時，如何停止並清理？
- 手動瀏覽器接管後應回到哪個生命週期階段？
- 登入衝突需要冷卻多久，並如何釋放 runtime 資源？
- 裝置現在是在初始化、運行任務還是休眠？

如果將這些生命週期邏輯也塞入 `TaskDef` callback，就會把現在的大型 `if/else` 改成一組
難以追蹤的 callback graph，複雜度並沒有消失。

---

## 5. Task Registry 應該設計到什麼程度

### 5.1 註冊單位是「可獨立排程與記錄完成」的任務

一個 registry entry 應對應一個可獨立回答以下問題的任務：

- 是否啟用？
- 現在是否 due？
- 是否有可用後端？
- 執行結果是完成、略過、重試、失敗還是被中斷？
- 成功後如何記錄？

「點擊」、「OCR」、「打開菜單」不是 registry task；它們是 task implementation 的內部步驟。

### 5.2 建議資料模型

```python
@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    order: int
    enabled_key: str | None
    due_policy: DuePolicy
    executors: Mapping[BackendKind, TaskExecutor]
    completion_policy: CompletionPolicy
    timeout_sec: float | None = None
    retry_policy: RetryPolicy = RetryPolicy.none()
    tags: frozenset[str] = frozenset()
```

需要特別避免以下設計：

- 不要用可變的顯示中文當作 `task_id`。
- 不要把大量 lambda 與隱性副作用直接塞進資料表。
- 不要用單一 `dict[str, Any]` 代替可驗證的 dataclass/protocol。
- 不要強迫 WS、ADB 與 Playwright 實作完全一樣；統一的應是輸入輸出合約。
- 不要將每個任務特例都擴充成 `TaskDefinition` 新欄位。

當特例真的需要獨立策略時，應用 `DuePolicy`、`CompletionPolicy` 或 executor 對象承擔，
而不是讓註冊表長成幾十個 optional field。

### 5.3 任務結果必須標準化

如果各任務繼續回傳 `True`、`False`、`None`、set、dict 或以 exception 代表不同語意，
註冊表仍然無法用通用執行器處理。建議統一為：

```python
class TaskOutcome(Enum):
    COMPLETED = auto()
    SKIPPED = auto()
    RETRYABLE_FAILURE = auto()
    PERMANENT_FAILURE = auto()
    INTERRUPTED = auto()

@dataclass(frozen=True)
class TaskResult:
    outcome: TaskOutcome
    detail: str = ""
    retry_after_sec: float | None = None
    completion_updates: Mapping[str, object] = field(default_factory=dict)
```

其中 `INTERRUPTED` 必須與任務失敗分開。使用者主動強制休眠不是遊戲任務失敗，
也不應因此污染錯誤統計或觸發相同的重試政策。

---

## 6. 狀態機、註冊表與現有 service 的邊界

建議整體責任如下：

```text
Dashboard / Master / Worker
              |
              | RuntimeEvent
              v
    DeviceRuntimeController          <- 每台裝置一個
      | phase + control_mode
      | dispatch(event)
      |
      +--> RuntimeStateMachine       <- 只決定合法轉移
      +--> RuntimeEffectExecutor     <- 執行建立/關閉/睡眠等副作用
      +--> TaskPipeline
             |
             +--> TaskRegistry      <- 任務定義與後端實作對照
             +--> TaskLedger        <- 本輪/今日完成進度
             +--> TaskExecutor      <- WS / ADB / Playwright 實際操作
```

現有 `runtime_services` 不需要被全部重寫。在過渡期，它們可以當作 effect executor 的現成動作：

- `initialize_runtime_device()` 是初始化 effect。
- `run_sleep_cycle()` 是休眠 effect。
- `handle_device_wakeup()` 是喚醒 effect。
- `_run_ws_phase_for_wake()` 是 WS phase executor。
- `daily_pipeline` 在過渡期可先作為單一 client-task executor。

狀態機只應編排這些動作，不應把 ADB、Playwright、OCR 或 WS protocol 細節寫入
transition callback。否則只是把 `main()` 的大函式搬進狀態機設定。

---

## 7. 是否使用現成狀態機套件

Python 生態有可用的現成方案，主要候選為：

| 方案 | 優點 | 風險/成本 | 本專案意見 |
|---|---|---|---|
| `transitions` | 輕量、guard/callback、hierarchical/locked 擴充、可畫圖 | callback 過度使用時容易隱藏控制流 | 若需導入套件，優先試點 |
| `python-statemachine` | 宣告式清楚、有模型驗證與 async 支援 | 引入新的宣告式 API 習慣 | 可作第二候選 |
| 自建 `Enum + transition()` | 依賴少、控制流完全顯性、容易單元測試 | 需自己做非法轉移、圖表與進階功能 | 第一版的預設建議 |

本文建議不要先因為「要做狀態機」就加入套件。可先用純函式轉移表試作：

```python
def transition(
    context: DeviceRuntimeContext,
    event: RuntimeEvent,
) -> TransitionDecision:
    """只計算下一狀態與所需 effect，不執行 I/O。"""
```

如果試作後出現以下需求，再換成 `transitions` 也不遲：

- 需要嵌套/階層狀態。
- 需要自動產生狀態圖。
- 需要大量 entry/exit callback。
- 需要套件提供的非法轉移診斷。

套件不會自動解決執行緒安全、訊號優先級、任務 cooperative cancellation、
ADB/Playwright 資源清理與 master/worker 傳輸語意。這些仍然是本專案必須自己定義的合約。

---

## 8. 過度設計的風險與防線

導入狀態機最常見的失敗不是功能寫不出來，而是形式化範圍過大。建議設下以下防線。

### 8.1 狀態數預算

第一版 runtime phase 不超過 8至10 個。如果出現十幾個以上，先檢查是否將 control mode、
observation、task progress 或 retry counter 誤當成 phase。

### 8.2 轉移不執行重型 I/O

transition function 必須可以在不連 ADB、不開瀏覽器、不連 WS 的情況下快速測試。
它回傳 effect intent，由 executor 在 transition 外執行副作用。

### 8.3 不一次重寫現有 service

先包裝既有動作，不改內部語意。若導入 FSM 的第一個 PR 就同時改寫休眠計算、
WS runner、daily pipeline 與 Playwright lifecycle，就已經失去可驗證性。

### 8.4 不把每個任務做成 class-based State Pattern

這會產生大量只包一個 `run()` 的小 class，並迫使閱讀者跨檔追蹤線性 pipeline。
任務預設是 registry entry + executor，只有自身真的存在複雜恢復轉移時才擁有小型 FSM。

### 8.5 儀表板文字不當作執行權威狀態

`task="休眠中"` 與 `step="準備喚醒"` 適合顯示，但不應讓執行流程用字串反向推斷狀態。
應由 typed runtime context 投影出 dashboard snapshot，而不是相反。

### 8.6 限制註冊表欄位膨脹

當 `TaskDefinition` 每增加一個任務就要增加一個 optional field，表示抽象邊界錯了。
可將三個以上共用的差異建模為 policy；只有單一任務使用的細節留在 executor 內。

---

## 9. 反對論點與回應

### 9.1 「現在可以運作，不需要動」

回應：現在能運作說明現有行為應被保留，不代表轉移邏輯容易修改。當每增加一個中斷訊號
都要檢查初始化迴圈、主迴圈、休眠 helper、WS runner 與 web session helper 時，維護風險已經很高。
改造原則應是先用測試凍結現有行為，而不是以全面重寫證明新架構。

### 9.2 「系統是排程輪詢，不是事件驅動，所以不需要 FSM」

回應：定時喚醒只是一種事件來源。系統同時還有 pause/resume、force sleep、manual launch/release、
web close、login conflict、connect failure、timeout 與 shutdown。有限事件並不排斥 FSM，反而是建立小型轉移表的好條件。

關鍵不是系統是否被稱為「事件驅動」，而是「同一事件在不同階段是否有不同合法行為」。
在本專案中，答案是肯定的。

### 9.3 「狀態機只是把 if/else 搬到表格」

回應：如果轉移仍然可在任意地方直接修改 phase，這個批評就是對的。有價值的 FSM 必須同時帶來：

- 單一 `dispatch(event)` 入口。
- 非法轉移可被拒絕與記錄。
- guard 與優先級可單元測試。
- 所有轉移有 `from/event/to/reason` 結構化 log。
- 儀表板可由權威 runtime context 生成快照。

如果不準備實現這些特性，確實沒有必要導入狀態機。

### 9.4 「用一個註冊表就可以解決」

回應：註冊表可以大幅降低新增任務的多點修改，這對本專案很重要。但它不定義強制休眠
如何從 WS、客戶端任務或手動操作中斷，也不定義異地登入後的冷卻與恢復路徑。
兩者的責任不同，不應互相取代。

### 9.5 「直接導入成熟套件會比自建安全」

回應：套件可以提供轉移驗證與工具，但不知道本專案的中斷語意、資源邊界與後端差異。
先用純函式建立正確的領域模型，再決定是否需要套件，風險更低。

---

## 10. 建議的漸進實施路徑

### 階段 0：只建立行為基線

不改架構，先為以下情境補特徵測試：

- 休眠中收到立即喚醒。
- WS 階段收到強制休眠。
- 暫停後收到手動開網頁。
- 手動操作結束後恢復原本休眠。
- 瀏覽器關閉後不影響 ADB 裝置。
- 初始化、WS fallback 與運行中發生異地登入。
- master 透過 worker 傳送同一命令時的等價行為。

本階段產出的不是新架構，而是改造不可破壞的合約。

### 階段 1：建立 typed context 與純轉移決策

新增 `RuntimePhase`、`RuntimeEvent`、`ControlMode`、`DeviceRuntimeContext` 與無 I/O 的
`transition()`。先只覆蓋少量轉移，並不取代現有主迴圈。

可先以 shadow mode 執行：現有程式仍決定行為，新 transition 同步計算「應該前往的狀態」，
若與實際不同只記錄 log。這可先暴露模型遺漏，而不改變裝置行為。

### 階段 2：先接管最高風險的中斷路徑

優先遷移：

- `FORCE_SLEEP`
- `PAUSE` / `RESUME`
- `MANUAL_LAUNCH` / `MANUAL_RELEASE`
- `LOGIN_CONFLICT`
- `SHUTDOWN`

這些事件會橫跨多個 phase，也是隱性分支最容易漏掉的地方。過渡期仍然重用現有
sleep/web session/service helper 執行實際副作用。

### 階段 3：將正常生命週期編排移入 controller

依序遷移：

```text
INITIALIZING -> WS_PHASE -> WAKING_CLIENT -> CLIENT_TASKS -> SLEEPING
```

完成後，`new_main_v2.main()` 應縮減為建立 dependency/context 與啟動 controller，不再包含
各種事件的實際處理細節。

### 階段 4：試作 Task Registry

不一次遷移全部任務。選擇三種代表性任務：

1. 有 WS 與 client 對照的任務。
2. 只有單一後端的任務。
3. 有特殊 due/completion schema 的任務。

先驗證能否用同一 `TaskDefinition` 和 `TaskResult` 合約表示這三類，再決定是否擴大。

### 階段 5：根據數據決定是否引入狀態機套件

試作後量測：

- transition table 有多少列？
- 非法轉移與中斷優先級是否容易自建？
- 是否真的需要階層狀態？
- 是否需要自動產生圖供維運與 debug？
- 自建框架是否開始重複 `transitions` 的功能？

再用實際維護成本決定保留自建純函式，或改採現成套件。

---

## 11. 驗證方式與成功標準

設計改造的成功不應以「新增多少 class」或「使用什麼套件」衡量，而應以以下結果衡量。

### 11.1 轉移可驗證

- 每一個 `RuntimeEvent` 在每一個 phase 都有明確策略：允許、忽略、延後或拒絕。
- 非法轉移不會靜默改變狀態。
- 強制休眠等高優先事件有 table-driven test 覆蓋所有可中斷 phase。
- 每次轉移都有結構化 `device/from/event/to/reason/timestamp` log。

### 11.2 維護範圍縮小

- 新增一個 runtime 中斷事件時，不需要手動穿插多個迴圈。
- 新增一個普通任務時，主要修改一個 registry entry、executor 與相關測試。
- `new_main_v2.main()` 不再承擔具體任務與資源清理細節。
- dashboard 狀態來自 typed context 投影，而不是反向影響執行。

### 11.3 行為沒有被改壞

- ADB 與 `web_h5` 的關閉/喚醒差異仍被保留。
- WS-first 與 client fallback 順序不變。
- pause、manual launch、force sleep 優先級符合行為基線。
- master 本機命令與 worker 遠端命令經過同一 event 語意。

---

## 12. 需要討論與拍板的地方

以下問題不應由實作者在程式裡臨時猜測，應先形成設計決策。

### 12.1 暫停是 phase 還是 control mode？

本文傾向 control mode，因為恢復時通常應保留原 phase。但需先確認：

- 暫停 WS 任務是原地繼續，還是重跑本輪 WS ledger？
- 暫停客戶端任務是否允許從當前 task 繼續？
- 暫停期間是否保留瀏覽器與 WS session？

### 12.2 手動操作是 control mode 還是獨立 phase？

手動模式可能需要獨立資源進出動作，比 pause 更像 phase。需確認從休眠與從 client task
進入手動操作時，退出後的 resume policy 是否一樣。

### 12.3 事件優先級與丟棄政策

建議先討論明確優先順序，例如：

```text
SHUTDOWN > FORCE_SLEEP > LOGIN_CONFLICT > MANUAL_LAUNCH > PAUSE > WAKE_OVERRIDE
```

但這只是候選，需以使用者預期與資源安全性拍板。還要決定低優先事件是丟棄、
保留到新 phase，還是回報 conflict。

### 12.4 中斷是否為 cooperative cancellation？

Python thread 不應被強制 kill，因此任務需在可控邊界檢查 cancellation token。需決定：

- 最大允許多久才回應強制休眠？
- 哪些長時間呼叫必須支援可取消 timeout？
- 任務被中斷時如何寫 ledger，避免誤記成完成？

### 12.5 休眠是「狀態」還是「排程器等待」？

對使用者與 runtime 而言，休眠是可觀測、可中斷且有 `next_wake_at` 的長期階段，本文傾向保留
`SLEEPING` phase。但等待本身應由 scheduler/timer 實作，不應讓 state machine 阻塞執行。

### 12.6 runtime context 哪些資料需要持久化？

不應預設整個 context 都寫磁碟。建議區分：

- 可重建：現在 phase、connection object、畫面 observation。
- 需持久：任務 ledger、必要的 cooldown deadline、可能的 resume intent。
- 只用於顯示：dashboard task/step/log snapshot。

需對程式異常終止後的恢復語意做明確規定，不是因為有 FSM 就自動獲得可恢復性。

### 12.7 Task Registry 是否同時取代排程器？

本文傾向「不取代」。Registry 描述 task 與 policy，scheduler 根據 context 挑選 due task。
若 registry 同時負責當前時間、特殊日、裝置角色、後端能力與完成回寫，它會快速變成新的上帝物件。

### 12.8 狀態機套件的採用門檻

團隊應先定義何時由自建轉移表切換到 `transitions`，例如：

- 轉移表超過某個可讀性門檻。
- 確定需要 hierarchical state machine。
- 狀態圖生成成為維運需求。
- 自建的 guard/callback/locking 已開始重複套件。

---

## 13. 建議的最小試點

不建議以「重構整個 `main()`」當作第一個實作階段。最小試點可限定為一台模擬裝置與
以下狀態/事件：

```text
RuntimePhase:
  WS_PHASE, WAKING_CLIENT, CLIENT_TASKS, SLEEPING

RuntimeEvent:
  WS_COMPLETED, CLIENT_READY, TASKS_COMPLETED,
  WAKE_DUE, FORCE_SLEEP
```

試點要求：

1. 轉移決策為純函式。
2. `FORCE_SLEEP` 覆蓋所有三個活躍 phase。
3. 使用 fake effect executor，不連接真實 ADB/Playwright/WS。
4. 以 table-driven test 列出所有合法與非法轉移。
5. 將實際主迴圈的狀態變化與試點模型比對，不立即接管。

若試點無法清楚表達現有行為，或只是把相同數量的分支搬家，應停止擴大並重新評估。
若它能將中斷優先級、恢復路徑與非法轉移變成短小的轉移表與測試，再進入正式遷移。

---

## 14. 最終意見

1. **導入狀態機，但限定在每台裝置的 runtime lifecycle。**
   原因不是程式碼很長，而是生命週期與橫跨階段中斷目前隱含在多層迴圈、區域變數、
   one-shot signal 與 exception path 的組合中。
2. **同時導入 Task Registry，但不讓它負責 runtime 轉移。**
   Registry 用來收斂任務順序、啟用條件、due policy、後端實作與完成記錄，不處理強制休眠、
   手動接管或異地登入恢復。
3. **第一版優先使用 typed context + 純轉移函式，不急著引入套件。**
   先證明領域邊界正確，當確定需要階層狀態、圖表或進階 callback/locking 時，再採用 `transitions`。
4. **以 shadow mode 與特徵測試漸進遷移，不全面重寫。**
   現有 runtime service 可先作為 effect executor 重用，每次只接管一小組轉移。
5. **試點必須允許得出「不值得繼續」的結論。**
   如果狀態機沒有降低中斷處理的修改點數、沒有讓轉移可測試，或增加的抽象成本大於收益，
   就應保留 registry/pipeline 改造而停止擴大 FSM。

一句話總結：

> **用狀態機管「裝置現在處於哪個可中斷的生命週期階段」，
> 用註冊表管「這個階段有哪些任務可以執行」；不讓任何一方成為新的巨型中心。**

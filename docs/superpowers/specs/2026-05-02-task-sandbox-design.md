# Task Sandbox — 通用任務開發/驗證 Harness

**Date:** 2026-05-02
**Status:** Approved (brainstorm)
**Author:** Eric (with Claude assist)

## 1. 問題

新增/修改一支「串接腳本」（lamp、sea、store、daily_gift、farm…）很麻煩，痛點集中在：

1. **介面不一**：`sea(ip, d)`、`get_skill_and_partner(d)`、`farm(d, ip, Cnn_model)`、`oracle(d, None, ip=ip, clf=…, rl_recorder=…)` 簽名各異；`daily_pipeline.py` 只能用 `lambda` 把每支硬接進來。
2. **排程/條件邏輯散落**：`is_record_expired` / `enable_X_manager` / `_DEVICE_SKIP_GUARDIAN` / 週幾小時判斷 一支寫一個版本。
3. **State guard 手刻**：`_guarded_run` / `_run_at_main_page` / streak 計數 在 pipeline 裡硬寫。
4. **檔案位置無公約**：top-level `Sea.py`、`everyday_mission/`、`farm/`、`opengold_v2/` 各自為政。
5. **沒有單支腳本的開發迴圈**：要驗一支腳本必須跑整顆 bot；改錯了發現得很慢。
6. **LLM 協助開發卡關**：請 LLM 用 Playwright 驅動驗證時，「遊戲開起來不在主頁」「進不到該任務頁」「邊跑邊出狀況」三件事讓 LLM 沒法穩定迭代。

開神燈（lamp）是 6 同時發生的最痛 case：邏輯仍未穩定，已有 `lamp-debug`、`playwright-lamp-test` 兩個 skill 在處理，但都 lamp 專用、沒一般化。

## 2. 目標 / 非目標

### Goals

- **G1** 一個通用 harness，能對任意 task 做「導到起點 → 跑 → 觀測」的開發迴圈
- **G2** 既有 task 程式碼（`opengold_v2.lamp_service`、`Sea.py`、`Store.py`…）一行不改即可被 harness 驅動
- **G3** trace 結構化（JSON event stream + 關鍵時刻截圖），LLM 可吃 JSON 推斷錯在哪
- **G4** Explorer 模式：只給目標，LLM+OCR 探索互動，產 decisions log + Python 草稿
- **G5** 為 Phase 2 鋪路：daily_pipeline 之後可改成遍歷 TaskSpec list，不再為每支 task 寫 boilerplate

### Non-Goals

- ❌ 一次重構 `daily_pipeline.py` 全部 20 支 task（留到 Phase 4）
- ❌ 即時互動 MCP server（batch trace 已夠用，先不做）
- ❌ 全程截圖序列（NAS I/O 災難；只在關鍵事件截圖）
- ❌ 取代 `lamp-debug` / `playwright-lamp-test` skill 在用戶終端的位置（這些 skill 之後改用 harness 為後端，但 skill 本身保留）

## 3. 架構

### 3.1 高層形狀

採用 **TaskSpec（宣告式 + 既有 function 零修改）** 路線：每支 task 寫一個 `TaskSpec` value object，runner 欄位指向既有 entry function。Harness 只跟 spec 對話。

```
                ┌──────────────────────────┐
   Spec 定義 ──→│  task_sandbox.runner     │
                │  ┌─run mode──┐           │
                │  │ verify    │           │
                │  │ explore   │           │
                │  └───────────┘           │
                └──────┬───────────────────┘
                       │ uses
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   navigator.py   trace/recorder  explorer/loop
   (NavTarget→    (event JSON +   (LLM+OCR
    actual nav)    screenshots)    decisions)
        │              │              │
        └────┬─────────┴──────┬───────┘
             ▼                ▼
       MonitoredDevice   既有 task fn
       (adb / web_h5)    (opengold_v2.lamp_service.run, ...)
```

3 個 harness 模式：

| Mode | 輸入 | 行為 | 產出 |
|---|---|---|---|
| `run` | TaskSpec | 導到 entry → 執行 runner → 跑 verifier(若有) | trace.jsonl + 關鍵截圖 |
| `verify` | TaskSpec | 跳過 runner，只跑 verifier | trace.jsonl + pass/fail |
| `explore` | 自然語言 goal + 可選 reference paths | LLM+OCR 互動驅動 | decisions.jsonl + script.py 草稿 |

### 3.2 檔案佈局

```
task_sandbox/
├── __init__.py
├── cli.py                  # python -m task_sandbox <mode> <task> --device <id>
├── spec.py                 # TaskSpec, NavTarget, Schedule, TaskContext, TaskResult
├── runner.py               # run / verify / explore 入口
├── navigator.py            # NavTarget → 實際導航；含「from anywhere → main page」recovery
├── trace/
│   ├── __init__.py
│   ├── recorder.py         # Recorder：event() / span() / assertion() / screenshot()
│   ├── video.py            # ADB screenrecord / Playwright video 兩端橋接
│   └── schema.py           # Event TypedDict / dataclass
├── explorer/
│   ├── __init__.py
│   ├── loop.py             # screenshot → ocr_index → llm_client → action 主迴圈
│   ├── ocr_index.py        # 整理 OCR 結果為 [(text, bbox, center)] list
│   ├── llm_client.py       # 預設 Haiku 4.5；anthropic SDK
│   ├── prompt.py           # 拼 system + goal + elements + history + references
│   └── codegen.py          # decisions.jsonl → script.py
└── tasks/
    ├── __init__.py         # TASK_REGISTRY: dict[str, TaskSpec]
    └── lamp.py             # LAMP = TaskSpec(name="開神燈", ...)

runs/                       # gitignored
└── lamp_2026-05-02_14-30-12_emulator-5554/
    ├── trace.jsonl
    ├── screenshots/
    │   ├── 003_stage_change.png
    │   └── 011_ocr_miss.png
    ├── video.mp4           # 可選 (--record-video)
    ├── decisions.jsonl     # explore mode only
    └── script.py           # explore mode only
```

依賴方向：
- `task_sandbox/` 不 import `daily_pipeline` / `new_main_v2`（避免循環）
- `task_sandbox/tasks/lamp.py` import `opengold_v2.lamp_service`（既有 task 不變）
- `daily_pipeline.py` 可選擇 import `task_sandbox.tasks.TASK_REGISTRY`（Phase 4）

## 4. 介面設計

### 4.1 TaskSpec

```python
@dataclass(frozen=True)
class TaskSpec:
    # 識別與 schedule
    name: str                              # "開神燈"
    entry: NavTarget                       # NavTarget.LAMP_PAGE
    schedule: Schedule                     # EveryHours(2)

    # 執行
    runner: Callable[[TaskContext], TaskResult]
    verifier: Callable[[TaskContext], VerifyResult] | None = None

    # 條件
    enabled_when: Callable[[str], bool] | None = None   # ip → bool
    skip_devices: frozenset[str] = frozenset()           # like _DEVICE_SKIP_GUARDIAN
    timeout_sec: float = 120.0

    # Explorer 用：給 LLM 看的程式碼參考
    references: tuple[str, ...] = ()       # ("opengold_v2/lamp_service.py", ...)
```

範例（lamp）：

```python
# task_sandbox/tasks/lamp.py
from task_sandbox.spec import TaskSpec, NavTarget, EveryHours, TaskContext, TaskResult
from opengold_v2 import LampService, OpenGoldConfig
import config_manager

def _lamp_runner(ctx: TaskContext) -> TaskResult:
    cfg = OpenGoldConfig()
    svc = LampService(ctx.device, cfg, device_ip=ctx.ip)
    ok = bool(svc.run(times=ctx.config.get("lamp_times", 1000), is_compare=True))
    return TaskResult(ok=ok)

def _lamp_enabled(ip: str) -> bool:
    return bool(config_manager.get_device_config(ip).get("lamp_check_interval"))

LAMP = TaskSpec(
    name="開神燈",
    entry=NavTarget.LAMP_PAGE,
    schedule=EveryHours(2),         # 配合 lamp_check_interval
    runner=_lamp_runner,
    enabled_when=_lamp_enabled,
    references=(
        "opengold_v2/lamp_service.py",
        "opengold_v2/ui_controller.py",
        "Open_gold_paddle_ocr.py",
    ),
)
```

### 4.2 NavTarget

Enum，從小開始長：

```python
class NavTarget(str, Enum):
    MAIN_PAGE = "main_page"
    LAMP_PAGE = "lamp_page"
    FARM_PAGE = "farm_page"
    STORE_PAGE = "store_page"
    ARENA_PAGE = "arena_page"
    SEA_PAGE = "sea_page"
    # 後續按需要新增
```

`navigator.py` 維護一個 `NAV_HANDLERS: dict[NavTarget, Callable[[TaskContext], None]]`：

- `MAIN_PAGE`：呼叫既有 recovery（`d.app_stop` 重啟 / 連點返回鈕 / `get_stage_with_check` 確認）
- `LAMP_PAGE`：先 `MAIN_PAGE`，再 `img_tools.click_str_by_server(d, "神燈")` 等等

`navigate_to(target)` 算法：
1. 用 `get_stage_with_check` 看現在在哪
2. 若已在 target，直接返
3. 否則先導 `MAIN_PAGE`，再呼 `NAV_HANDLERS[target]`
4. 連續 3 次失敗 → raise `NavigationFailed`，trace 記錄理由 + 截圖

### 4.3 Schedule 型別

```python
class Schedule(Protocol):
    def should_run(self, ip: str, now: datetime, last_run: datetime | None) -> bool: ...

@dataclass(frozen=True)
class Always: ...                          # 每次 wake 都跑
@dataclass(frozen=True)
class EveryHours: hours: int               # last_run + n hours 後可跑
@dataclass(frozen=True)
class DailyOnce:                           # 一天一次
    reset_hour: int = 4                    # 4am 重置（配合遊戲）
@dataclass(frozen=True)
class WeeklyOn:                            # 指定星期幾
    days: frozenset[int]                   # {0,1,2,3,4} = Mon-Fri
@dataclass(frozen=True)
class HourWindow:                          # 限定時段
    start_hour: int; end_hour: int
@dataclass(frozen=True)
class Custom:                              # 逃生口
    fn: Callable[[str, datetime, datetime | None], bool]
```

可組合：`AndSchedule(WeeklyOn({0,1,2,3,4}), HourWindow(20, 23))` = 工作日晚上。

### 4.4 TaskContext / TaskResult

```python
@dataclass
class TaskContext:
    device: MonitoredDevice
    ip: str
    cnn_model: Any
    recorder: Recorder
    config: dict                  # config_manager.get_device_config(ip) 結果
    timeout_at: float             # time.time() 上限

@dataclass
class TaskResult:
    ok: bool
    reason: str = ""
    artifacts: dict = field(default_factory=dict)   # 自由欄位

@dataclass
class VerifyResult:
    ok: bool
    checks: list[tuple[str, bool, str]]   # (name, passed, detail)
```

### 4.5 Trace 事件 schema

```python
class TraceEvent(TypedDict):
    ts: float                     # unix
    seq: int                      # session 內 序號
    kind: str                     # "click"|"swipe"|"screenshot"|"ocr"|"wait_for"
                                  # |"stage_check"|"assertion"|"nav_step"|"error"|"span_start"|"span_end"
    args: dict                    # kind-specific
    stage_before: str | None
    stage_after: str | None
    ok: bool
    elapsed_ms: int
    screenshot_path: str | None   # 相對 runs/<run-id>/
    parent_span: str | None       # span 巢狀
```

**截圖觸發規則**（D 模式：limited screenshots）：
- `kind == "stage_check"` 且 `stage_before != stage_after`
- `kind == "assertion"` 且 `ok == False`
- `kind == "ocr"` 且 `ok == False`（target text not found）
- `kind == "error"`
- 顯式 `recorder.screenshot("reason")`

Recorder API：

```python
class Recorder:
    def event(self, kind: str, **args) -> None: ...
    def assertion(self, name: str, ok: bool, detail: str = "") -> None: ...
    def screenshot(self, reason: str) -> str: ...
    @contextmanager
    def span(self, name: str): ...
```

### 4.6 Video 錄製

`--record-video` flag 開啟。

- ADB backend：`task_sandbox/trace/video.py` 透過 `adb shell screenrecord /sdcard/<run>.mp4` + `adb pull` 在 session 結束時取回。3 分鐘 chunk 串接（screenrecord 預設上限）。
- web_h5 backend：在建 Playwright `BrowserContext` 時加 `record_video_dir=runs/<run>/`，自動產生。
- 影片不在 trace 事件層處理，是 session-level artifact；trace.jsonl 第一行記 `{"kind": "video_start", ...}`，最後一行 `{"kind": "video_end", "path": "video.mp4"}`，方便對 timestamp。

### 4.7 Explorer 模式

**目的**：當你還沒寫 runner function、但知道要做什麼時，讓 LLM+OCR 探索一遍，產出草稿。

**主迴圈**（`explorer/loop.py`）：

```
1. screenshot
2. ocr_index = OCR(image)  # [(text, bbox, center), ...]
3. action = llm_client.decide(
       goal, elements=ocr_index, history=recent_actions, references=ref_snippets
   )
4. execute(action)         # click_text / click_xy / swipe / wait / ocr_check / done
5. recorder.event(...) → decisions.jsonl
6. if action == done or step_count > MAX_STEPS or timeout: break
7. goto 1
```

**模型選擇**：預設 `claude-haiku-4-5-20251001`。決策複雜度低（從 N 個元素挑一個 + 動作型別 + 簡單 rationale），不需要 Opus/Sonnet。`llm_client.py` 透過 env var `TASK_SANDBOX_LLM_MODEL` 可換。

**Prompt 結構**：

```
[system]
You drive a mobile game by issuing one action per turn. You see OCR-detected
elements (text + screen coordinates) and recent action history. Output JSON only.

Available actions:
- {"action": "click_text", "text": "..."}
- {"action": "click_xy", "x": int, "y": int}
- {"action": "swipe", "x1": int, "y1": int, "x2": int, "y2": int}
- {"action": "wait", "seconds": float}
- {"action": "ocr_check", "text": "..."}      # assert visible; if so, success
- {"action": "done", "success": bool, "reason": "..."}

[user]
Goal: <自然語言目標>

Reference patterns from existing code (similar tasks):
<從 spec.references 抽出的關鍵 click/wait/check snippet>

Recent history (last 5 actions):
<從 decisions.jsonl tail>

Current screen elements:
<ocr_index 排序好的列表>

Decide ONE action.
```

**Reference 抽取**（`prompt.py`）：對 `references` 路徑做 regex 抓 `click_str_by_server(d, "...")` / `wait_for_any_text` / `d.click(x, y)` / `time.sleep(...)` 共 ~15 行 snippet（不貼全檔，省 token）。

**Codegen**（`codegen.py`）：把 decisions.jsonl 翻成 script：

```python
# Generated by task_sandbox.explorer at 2026-05-02 14:30:12
# Goal: 進入神燈頁並按下召喚
def explored_lamp(d, ip):
    img_tools.click_str_by_server(d, "神燈")
    time.sleep(0.5)
    d.click(274, 841)
    time.sleep(2)
    if not img_tools.wait_for_any_text(d, ["召喚"], timeout=10):
        return False
    img_tools.click_str_by_server(d, "召喚")
    return True
```

action → 程式碼映射：
- `click_text(text)` → `img_tools.click_str_by_server(d, "{text}")`
- `click_xy(x,y)` → `d.click({x}, {y})`
- `swipe` → `d.swipe(...)`
- `wait(s)` → `time.sleep({s})`
- `ocr_check(text)` → `if not img_tools.wait_for_any_text(d, ["{text}"], timeout=10): return False`
- `done(True)` → `return True`
- `done(False)` → `return False`

### 4.8 CLI

```
python -m task_sandbox run lamp --device emulator-5554
python -m task_sandbox run lamp --device emulator-5554 --record-video
python -m task_sandbox verify lamp --device emulator-5554
python -m task_sandbox explore --goal "進入神燈頁並按下召喚" \
    --device emulator-5554 \
    --refs opengold_v2/lamp_service.py,opengold_v2/ui_controller.py
python -m task_sandbox list                 # 列出已註冊的 TaskSpec
```

通用 flag：`--device`、`--max-steps`、`--timeout`、`--record-video`、`--out runs/`、`--llm-model`

## 5. 與既有架構的關係

### 5.1 既有 task 模組（不變）

`opengold_v2/`、`farm_v2/`、`Sea.py`、`Store.py`、`daily_gift_task.py`、`Skill.py`、`miner/`、`new_battle.py`、`rank_events.py` ── **零修改**。`task_sandbox/tasks/<name>.py` 寫薄包裝把它們的 entry 函式包進 `TaskSpec.runner`。

### 5.2 既有 helper 重用

| Harness 元件 | 重用既有 |
|---|---|
| `navigator.MAIN_PAGE` | `game_actions.stage_guard.get_stage_with_check`、`adb_operations.start_game_by_icon` |
| `navigator.LAMP_PAGE` 等 | `img_tools.click_str_by_server`、`wait_for_any_text` |
| `Recorder.screenshot` | `MonitoredDevice.screenshot` |
| Explorer OCR | `img_tools.analyze_text_via_server`（既有 OCR 端點） |
| Explorer LLM | 新檔 `explorer/llm_client.py`，用 `anthropic` SDK |

### 5.3 既有 lamp-debug / playwright-lamp-test skill

兩個 skill 保留，但實作改為呼叫 harness：
- `lamp-debug` → 內部呼 `python -m task_sandbox run lamp` 並解析 trace.jsonl 給用戶看
- `playwright-lamp-test` → web_h5 backend 的 lamp run，外加它原本的「對齊真實帳號」流程

短期內兩個 skill 可繼續以舊路徑運作；harness 上線後另開 PR 切換。

## 6. 階段路線

### Phase 1：Harness 骨架 + lamp（本 spec 主要範圍）

交付物：
- `task_sandbox/spec.py`（TaskSpec、Schedule、NavTarget、TaskContext、TaskResult）
- `task_sandbox/runner.py`（只實作 `run` 模式）
- `task_sandbox/navigator.py`（MAIN_PAGE + LAMP_PAGE 兩個目標）
- `task_sandbox/trace/recorder.py` + `schema.py`（不含 video）
- `task_sandbox/cli.py`（`run` + `list` 兩個 subcommand）
- `task_sandbox/tasks/lamp.py`
- `tests/test_task_sandbox_*.py` 基本測試（FakeDevice + Recorder）
- `runs/` 加進 `.gitignore`

驗收：
- `python -m task_sandbox run lamp --device <真實設備>` 跑得起來
- 失敗時 trace.jsonl 內有可診斷的 event + 至少一張關鍵截圖
- 用 LLM 看 trace.jsonl 能說出哪步壞掉（人 + LLM 各跑一次測試）

### Phase 2：Verify + 多支 task

交付物：
- `runner.py` 加 `verify` 模式
- `tasks/farm.py`、`tasks/store.py`、`tasks/oracle.py`、`tasks/sea.py`
- `navigator.py` 補 `FARM_PAGE`、`STORE_PAGE`、`SEA_PAGE`
- 上述 4 支 task 的 `verifier` function（簡單斷言：在預期頁、預期狀態 flag 為 true）

驗收：每支都能 `run` 與 `verify` 各跑一次成功。

### Phase 3：Explorer

交付物：
- `task_sandbox/explorer/`（loop / ocr_index / llm_client / prompt / codegen）
- `runner.py` 加 `explore` 模式
- `cli.py` 加 `explore` subcommand
- 用一個非 lamp 的小目標（例如「進入家園頁並點比格先生」）做端到端煙霧測試

驗收：跑出 decisions.jsonl + script.py，script.py 用 `python` 跑下去能達成相同目標（容忍 1 次重試）。

### Phase 4：Video 整合

交付物：
- `task_sandbox/trace/video.py`（ADB / web_h5 兩條路）
- CLI flag `--record-video`
- ffmpeg 串接（screenrecord 3 分鐘 chunk）

驗收：lamp 跑一次能拿到 video.mp4 + trace timestamps 對得上。

### Phase 5：daily_pipeline 對齊（後續另案，不在本 spec）

把 daily_pipeline 改成遍歷 `TASK_REGISTRY`、每支 task 自動套用 stage guard / streak 計數 / state update。每支既有 task 補上 `TaskSpec`、原 lambda 移除。預計另開一個 GSD phase，跨 1~2 個 PR 收斂。

## 7. 測試策略

| 層級 | 工具 | 範圍 |
|---|---|---|
| Unit | pytest | `Schedule.should_run` 在不同 `now` / `last_run` 下的決策；NavTarget enum；Recorder 在 FakeDevice 上的 event/screenshot |
| Integration | pytest + FakeDevice | `runner.run(spec=NoOpTask, device=FakeDevice)` 端到端，驗證 trace.jsonl 結構與內容 |
| E2E | 手動 / smoke | 真實設備跑 `python -m task_sandbox run lamp`，看 trace 與截圖 |
| Explorer | pytest + StubLLM | LLM 用 stub 回固定動作序列，驗證 codegen 輸出符合預期 |

`FakeDevice`：`MonitoredDevice` 介面的 in-memory 假實作，`screenshot()` 回固定圖片，`click()` 記錄到內部 list。已有 `tests/` infra（`conftest.py` 加 sys.path），新增 `tests/fakes/device.py`。

## 8. 風險 / 開放問題

| 風險 | 緩解 |
|---|---|
| **NavTarget 數量爆炸**：每加一個任務頁就要新 enum + handler | 限制：只在「task entry」需要時才加，不為了完整覆蓋 UI 而加 |
| **Explorer 產的 script.py 品質低**：點到不該點的、誤判完成 | script.py 永遠是「草稿」，需人/LLM 二次審；不直接拿來進 production registry |
| **既有 task fn 簽名不一致**：包成 runner 時各自要寫 lambda | 接受。spec.runner 是包裝層，每支 task 寫一次 |
| **`MonitoredDevice` 在 web_h5 下行為差異**：click 座標系不同 | Phase 1 只測 ADB；web_h5 在 Phase 2 接 verify 模式時驗證；若有差異開 follow-up issue |
| **ADB screenrecord 3 分鐘上限** | Phase 4 才做；用 chunk + ffmpeg concat |
| **OCR server 連不到** | 既有 `img_tools` 已有 circuit breaker + fallback，沿用 |
| **`runs/` 大小** | gitignore；CLI 加 `--out` 可改路徑（指向本機 SSD） |

## 9. 開放問題（不影響 Phase 1 設計）

- Explorer 的 LLM 是否要支援多供應商（除 anthropic 外加 openai / 自架 vLLM）？目前 env var 切換，但 client 介面只有 anthropic 一條路。Phase 3 視需要再抽 protocol。
- `daily_pipeline` 的 `_DEVICE_SKIP_GUARDIAN` 等 device-specific exclusion 在 Phase 5 應該如何最終呈現（`skip_devices` 已在 spec，但一些更細的 device-conditional logic 需要 `enabled_when`）？Phase 5 開始時定。
- Explorer codegen 是否要產 `TaskSpec` 而非裸 function？目前只產 function，spec 仍要人手寫。可以後續加。

## 10. 附錄：與既有 GSD workflow 的銜接

本 spec 接著走 GSD：
- 由 writing-plans skill 產生 `.planning/phases/NN-task-sandbox/` 的 PLAN / RESEARCH / VALIDATION 等檔
- Phase 1 的 PR 範圍：`task_sandbox/` + `tests/test_task_sandbox_*.py` + `.gitignore` + `tasks/lamp.py`
- 每階段依 GSD 範本走 checkpoint

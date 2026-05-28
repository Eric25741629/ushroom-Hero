# OpenGold V2 優化建議

> 基於 `opengold_v2/` 全部 10 個 .py 檔案與 README.md 的靜態分析。

---

## 一、架構設計

### 1.1 總結：重構方向正確，但仍有改進空間

V2 從 1400 行單檔拆分為 8 模組，方向正確。以下為進一步建議。

### 1.2 `run()` 主迴圈過於龐大

**問題：** `LampService.run()` 約 130 行，混合了：
- 迴圈控制邏輯
- REENGAGE 策略（`failed_reengage_streak`、`last_drop_ts`、grace window）
- RENAVIGATE 處理
- HANDLE_POPUP 處理
- 統計/日誌

**建議：** 將 `run()` 拆為更小的策略方法：

```python
def run(self, times: int = 1000, is_compare: bool = True):
    ...
    while ...:
        if self.ui.is_lamp_sell_page(): ...
        action = self.loop_state.tick(lamp_count, has_popup)
        handler = {
            LampLoopAction.WAIT:           self._on_wait,
            LampLoopAction.HANDLE_POPUP:   self._on_popup,
            LampLoopAction.REENGAGE_AUTO:  self._on_reengage,
            LampLoopAction.RENAVIGATE:     self._on_renavigate,
        }[action]
        if not handler(ctx):
            break
```

每個 handler 接收一個 `LampRunContext` dataclass，封裝 `lamp_count`、`has_popup`、`last_seen_count` 等共享狀態，避免 `run()` 中十幾個區域變數。

### 1.3 `_return_to_original_equipment()` 硬編碼座標

**問題：** 該方法使用 3 組硬編碼 `(x, y)` 點擊序列：
```python
self.ui.click_and_wait(419, 720, 3)
self.ui.click_and_wait(272, 796, 1)
self.ui.click_and_wait(281, 350, 1)
```
這些座標與 `ui_controller.py` 中 `open_stage_menu()` 部分重複，且無法配置。

**建議：**
1. 在 `OpenGoldConfig` 中定義 `return_to_equip_sequence: List[Tuple[int, int, float]]`
2. 或將「返回原始裝備」的完整動作封裝為 `UIController` 的一個方法，避免 `LampService` 直接操作座標

### 1.4 `DeviceDetector` 職責不清

**問題：**
- 類名為 `DeviceDetector`，但實際偵測的是「裝備類型」（連閃），不是「裝置」
- 只有一個方法 `detect_lian_shan_equip`，且只需要一個 `List[str]` 參數
- 整個類可以縮減為一個 `@staticmethod` 或獨立函數

**建議：**
```python
# 重命名為 EquipmentTypeDetector 或直接用函數
def detect_lian_shan(stage_texts: List[str], keywords: List[str]) -> bool:
    return any(kw in text for text in stage_texts for kw in keywords)
```

### 1.5 缺少抽象介面

**問題：** `LampService` 依賴兩個注入函數 (`analyze_skill_fn`, `analyze_stage_fn`)，這是好的做法。但 `UIController` 直接耦合 `device` 物件，且 `ScreenshotLogger` 也直接操作 `device`。

**建議：** 定義 protocol/ABC：
```python
from typing import Protocol

class GameDevice(Protocol):
    def click(self, x: int, y: int) -> None: ...
    def screenshot(self, format: str = 'opencv') -> np.ndarray: ...
```

這樣單元測試可以用 mock device，不需要連接實機。

---

## 二、錯誤處理

### 2.1 過度寬泛的 `except Exception`

**問題：** 幾乎所有 `try/except` 都捕獲 `Exception`，且多數只是 `print` 或 `logger.warning` 後繼續執行。這會隱藏真實錯誤。

**高風險位置：**

| 位置 | 風險 |
|------|------|
| `LampService._detect_lian_shan_equip()` | 失敗時預設回傳 `True`，可能導致比較邏輯錯誤 |
| `UIController.click_auto_mode_button()` | 失敗時靜默跳過，主迴圈無法感知自動模式未啟動 |
| `OCRParser.get_first_text_from_skill_result()` | 最外層 `except Exception: return ''`，所有解析錯誤被吞掉 |
| `ScreenshotLogger._cleanup_old_files()` | 失敗時靜默繼續，可能導致磁碟空間耗盡 |

**建議：**
1. **分級捕獲**：`except (ValueError, KeyError)` 捕獲預期錯誤，`except Exception` 用於最後防線並記錄完整 traceback
2. **失敗傳播**：關鍵路徑（如 OCR 解析失敗）應回傳明確的錯誤狀態，而非靜默吞掉
3. **重試機制**：網路呼叫（HTTP OCR）應加入重試而非直接放棄

```python
# 改善前
except Exception as e:
    logger.warning(f"[LampService] 偵測連閃裝備失敗: {e}，預設為 True")
    return True

# 改善後
except (requests.RequestException, TimeoutError) as e:
    logger.warning(f"[LampService] 偵測連閃裝備網路失敗: {e}")
    return self.has_lian_shan_equip  # 保留上次結果，而非強制 True
except Exception:
    logger.exception("[LampService] 偵測連閃裝備未預期錯誤")
    return self.has_lian_shan_equip
```

### 2.2 `_detect_lian_shan_equip` 失敗預設值不合理

**問題：** 連閃偵測失敗時預設 `return True`，但這意味著「假設擁有連閃裝備」會影響 `_cap7_sum` 的計算方式，可能導致錯誤的比較結果。

**建議：** 失敗時保留上次值 (`self.has_lian_shan_equip`)，首次偵測失敗則預設 `False`（保守策略）。

### 2.3 `process_single_lamp` 的 finally 區塊

**問題：**
```python
try:
    ...
    return True  # 多個 early return
finally:
    self._record_lamp_consumption(before_count)
```
`finally` 中呼叫 `_record_lamp_consumption`，該方法內部又呼叫 `self.ui.get_gold_num()` 做 OCR。如果 `process_single_lamp` 本身因 OCR 失敗而 return，finally 中再次 OCR 可能也失敗，形成遞迴式錯誤。

**建議：** 在 finally 中加入保護：
```python
finally:
    try:
        self._record_lamp_consumption(before_count)
    except Exception:
        logger.debug("[LampService] lamp consumption recording failed in finally")
```

---

## 三、效能

### 3.1 重複截圖

**問題：** 在 `process_single_lamp()` 中：
1. `_log_screenshot(prefix="lamp", suffix="start")` — 截圖 1
2. `self.ui.click_lamp_button()` 內部 `click_and_wait` 不截圖
3. `_log_screenshot(prefix="lamp", suffix="opened")` — 截圖 2
4. `self.ui.get_skill_roi()` 內部 `capture_screenshot()` — 截圖 3
5. `_log_roi(skill_roi, "skill_roi")` — 寫入磁碟
6. `self.analyze_skill_fn(skill_roi)` — HTTP 上傳
7. `_log_screenshot(prefix="lamp", suffix="incomplete_ocr")` 可能 — 截圖 4

在 `_execute_upgrade_sequence()` 中還有更多截圖。單次開燈最多 **6-8 次截圖 + 6-8 次磁碟寫入**。

**建議：**
1. **快取截圖**：在流程起始截圖一次，傳給需要的子方法，直到畫面確實改變才重新截圖
```python
def process_single_lamp(self, is_compare: bool = True) -> bool:
    img = self.ui.capture_screenshot()  # 一次截圖
    self._log_screenshot(img, prefix="lamp", suffix="start")
    
    if self._handle_sell_page_if_present(img, "當前在全部出售頁面"):
        return True
    ...
    img = self.ui.capture_screenshot()  # 點擊後重新截圖
    skill_roi = self.ui.get_skill_roi(img)  # 傳入已有截圖
```

2. **異步截圖**：截圖記錄改為背景執行（`threading.Thread`），不阻塞主流程
3. **按需截圖**：設定 `screenshot_level`（0=關閉, 1=關鍵節點, 2=全部），生產環境可關閉

### 3.2 `_read_lamp_count_robust` 多次 OCR 讀取

**問題：** 每個 tick 讀取 3 次神燈數量（每次都是截圖 + OCR），間隔 0.15 秒。在一個 tick 中：
- `_read_lamp_count_robust`: 3 次 OCR ≈ 1.5-3 秒
- `_detect_popup`: 1 次截圖 + HSV 分析 ≈ 0.3 秒
- `is_lamp_sell_page`: 1 次截圖 + 像素比對

一個 tick 最少 2-3 秒，加上 `_adaptive_wait` 的 1-2 秒，每個迴圈約 4-5 秒。

**建議：**
1. **共用截圖**：一次截圖後，從同一張圖讀取 gold_num 和 popup detection
2. **降低多幀讀取次數**：3 次太多，2 次（agree=2）已足夠防禦單幀誤讀
3. **條件性多幀**：只在值異常（突然上升/下降超過閾值）時才做多幀確認

### 3.3 `ScreenshotLogger._cleanup_old_files` 效能

**問題：** 每次存圖都呼叫 `_cleanup_old_files`，該方法列出所有檔案、排序、刪除。當截圖數量接近上限時（100 張），每張圖都要 list + sort 整個目錄。

**建議：**
1. **計數器方式**：用一個 `self._file_count` 追蹤，只在超過上限時才清理
2. **批次清理**：每 N 張圖清理一次，而非每張都清理
3. **使用 `os.scandir`** 替代 `os.listdir`（效能更好，尤其在檔案多時）

### 3.4 `_has_text` 每次都做完整 OCR

**問題：** `click_auto_mode_button()` 和 `click_start_confirm()` 都先呼叫 `_has_text()` 做一次完整 OCR 探測，如果存在再呼叫 `click_str_by_server`（內部可能再做一次 OCR）。相當於按鈕點擊前做 2 次 OCR。

**建議：** 合併為一次操作，或在 `_has_text` 中快取最近的 OCR 結果（TTL 約 2 秒）。

---

## 四、可維護性

### 4.1 Magic Numbers

**問題：** `ui_controller.py` 中有大量硬編碼座標：
```python
self.click_and_wait(447, 801, 2)   # navigate_to_lamp
self.click_and_wait(273, 560, 2)   # exit_lamp
self.click_and_wait(271, 576, 5)   # click_lamp_button
self.click_and_wait(227, 798, 1)   # click_sell_button
self.click_and_wait(376, 798, 0.3) # click_keep_button
self.click_and_wait(518, 16, 1)    # open_stage_menu
self.click_and_wait(281, 350, 2)   # stage select
click_y = 412 + (index - 1) * 49   # select_stage 動態計算
```

這些座標完全不可配置，且沒有任何文件說明它們對應的 UI 元素。

**建議：** 全部移入 `OpenGoldConfig`：
```python
@dataclass
class OpenGoldConfig:
    # ...existing...
    
    # 按鈕座標
    navigate_click_1: Tuple[int, int] = (447, 801)
    navigate_click_2: Tuple[int, int] = (281, 636)
    exit_click_1: Tuple[int, int] = (447, 801)
    exit_click_2: Tuple[int, int] = (273, 560)
    lamp_button: Tuple[int, int] = (271, 576)
    sell_button: Tuple[int, int] = (227, 798)
    keep_button: Tuple[int, int] = (376, 798)
    stage_base_y: int = 412
    stage_step_y: int = 49
```

### 4.2 `config.py` 的 `save_to_file` 不支援複雜類型

**問題：** `OpenGoldConfig` 包含 `Set[FrozenSet[str]]`、`Tuple`、`Dict[FrozenSet, str]` 等類型，但 `save_to_file` 直接使用 `json.dump(self.__dict__)`。這些類型無法直接序列化為 JSON。

```python
# 以下欄位在 JSON 序列化時會報錯或丟失：
unwanted_combos: Set[FrozenSet[str]]      # set/frozenset → TypeError
canonical_pair: Dict[FrozenSet[str], str] # frozenset key → TypeError
lamp_sell_page_profiles: Tuple            # tuple → list（可接受但不精確）
```

**建議：** 自定義 encoder：
```python
class ConfigEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (set, frozenset)):
            return {"__set__": True, "items": sorted(obj)}
        if isinstance(obj, tuple):
            return {"__tuple__": True, "items": list(obj)}
        return super().default(obj)
```
或使用 `dataclasses.asdict()` 配合自定義轉換。

### 4.3 `_AUTO_RETRY_EVERY` 硬編碼為 2

**問題：** `LampService._AUTO_RETRY_EVERY = 2` 控制「每跳過 2 次就重試自動模式」，但這個值無法配置，且與 `skip_incomplete_limit` 的邏輯緊耦合。

**建議：** 移入 `OpenGoldConfig`。

### 4.4 `LampState` 放在 `models.py` 但行為複雜

**問題：** `LampState` 在 `models.py` 中定義，但它不只是資料結構，還包含業務邏輯（`should_stop_for_incomplete_ocr`、`record_ocr_success` 等）。同時 `lamp_loop_state.py` 中的 `LampLoopState` 也管理狀態，兩者的職責邊界模糊。

**建議：**
1. `LampState` 重命名為 `LampSessionState`（表示一次開燈 session 的狀態）
2. `LampLoopState` 重命名為 `LampTickDecider`（表示每個 tick 的決策器）
3. 在文件中明確說明兩者的差異

### 4.5 型別標註不一致

**問題：**
- `_read_pairs_from_rois` 參數 `rois: List[np.ndarray]` 但傳入的是 `List[np.ndarray]`（正確）
- `get_original_rois` 回傳 `list` 但應為 `List[np.ndarray]`
- `save_screenshot` 回傳 `Optional[str]` 但 docstring 沒說明
- `LampLoopAction` 使用 Enum 但值是 string，未被使用

**建議：** 統一使用精確型別標註，並啟用 mypy 嚴格模式檢查。

### 4.6 測試困難

**問題：** README 聲稱「各模組可獨立測試」，但實際上：
- `UIController` 直接依賴 `device` 物件和 `time.sleep()`
- `LampService` 依賴 `img_tools`（外部模組）的 HTTP 呼叫
- `OCRParser` 依賴 `numpy`（可接受）
- 沒有任何 `tests/` 目錄

**建議：**
1. 建立 `tests/` 目錄
2. 為 `OCRParser`、`SkillEvaluator`、`LampLoopState` 建立純邏輯單元測試（不需要 device mock）
3. 為 `UIController` 建立 mock-based 測試
4. 加入 CI 整合（GitHub Actions / pre-commit）

---

## 五、其他具體問題

### 5.1 `ComparisonDecision` Enum 未使用

`models.py` 中定義了 `ComparisonDecision` 但整個 codebase 從未引用。應移除或改用。

### 5.2 `get_roi_for_device` 硬編碼裝置識別碼

```python
def get_roi_for_device(self, device_ip: str = None) -> List[...]:
    if device_ip and 'adb-fc65396d-4LPqmI._adb-tls-connect._tcp' in device_ip:
        return self.orig_roi_phone
    return self.orig_roi_computer
```

這個 ADB 識別碼是特定裝置的，應改為配置化：
```python
# 在 OpenGoldConfig 中
phone_device_patterns: List[str] = field(default_factory=lambda: [
    'adb-fc65396d-4LPqmI._adb-tls-connect._tcp'
])
```

### 5.3 `select_stage` 的 Y 座標計算

```python
def select_stage(self, index: int):
    if index == 0:
        self.click_and_wait(281, 350, 1)
    else:
        click_y = 412 + (index - 1) * 49
        self.click_and_wait(266, click_y, 1)
```

index=0 的 X 座標是 281，index>0 的 X 座標是 266，且 Y 座標基線不同（350 vs 412）。這暗示 index=0 點擊的是不同的 UI 元素（可能是「當前選中」的高亮區域）。應加入註解說明。

### 5.4 `_cap7_sum` 中 `float()` 轉型冗餘

```python
lian_shan_sum = float(skill_map.get('連', 0.0)) + float(skill_map.get('爆', 0.0))
```

`skill_map` 的值型別已經是 `float`（由 `Equipment.to_map()` 保證），`float()` 轉型是防禦性程式碼但應在 `to_map()` 中保證型別正確，而非在每個使用處重複轉型。

### 5.5 `_build_details` 中重複轉型

```python
try:
    detail.rolled_prob = None if r is None else float(r)
except Exception:
    detail.rolled_prob = None
    detail.rolled_unknown = True
```

`r` 來自 `rolled_map.get(s)`，而 `rolled_map` 是 `Dict[str, float]`，值應該已經是 float。如果確實存在非 float 的情況，應在 `Equipment.to_map()` 處理，而非在比較器中重複 try/except。

### 5.6 `click_and_wait` 中的 `inspect.currentframe()`

```python
def click_and_wait(self, x: int, y: int, wait_time: float = 1.0):
    try:
        reason = inspect.currentframe().f_back.f_code.co_name
    except Exception:
        reason = "?"
```

`inspect.currentframe()` 在某些 Python 實作（如 PyPy）中可能不可用，且效能開銷不小。如果只是為了日誌，可以改用 `logging` 的 `stacklevel` 參數或明確傳入 `reason`。

---

## 六、優先級建議

| 優先級 | 項目 | 原因 |
|--------|------|------|
| **P0** | 修復 `save_to_file` 序列化問題 | 使用者儲存配置時會崩潰 |
| **P0** | 修復 `_detect_lian_shan_equip` 失敗預設值 | 會導致比較邏輯錯誤 |
| **P1** | Magic numbers 移入 config | 影響不同裝置適配 |
| **P1** | 減少重複截圖 | 影響效能（每 tick 節省 1-2 秒） |
| **P1** | 錯誤處理分級 | 隱藏真實錯誤，難以除錯 |
| **P2** | `run()` 拆分 | 可維護性 |
| **P2** | 建立測試 | 長期品質保證 |
| **P2** | `DeviceDetector` 重命名/簡化 | 程式碼清晰度 |
| **P3** | Protocol 抽象化 | 測試友善度 |
| **P3** | 型別標註統一 | mypy 嚴格模式 |

---

## 七、整體評價

**做得好的地方：**
- 模組拆分合理，職責邊界清晰
- `LampLoopState` 純狀態機設計，可獨立測試
- `OpenGoldConfig` 集中管理配置，支援 JSON 持久化
- `_read_lamp_count_robust` 多幀共識讀取防禦 OCR 誤讀
- `_REENGAGE_PROGRESS_GRACE_SEC` 防止在 auto 模式正常間隙時誤觸 reengage
- Screenshot Logger 自動清理機制

**主要改進方向：**
1. **減少 I/O**：截圖快取、OCR 結果快取、異步截圖
2. **強化錯誤處理**：分級捕獲、失敗傳播、重試機制
3. **配置化**：所有座標、閾值、重試次數移入 config
4. **可測試性**：建立 tests/、mock device protocol

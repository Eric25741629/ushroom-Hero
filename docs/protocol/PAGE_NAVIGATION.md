# 頁面切換協議與自動化（2026-05-20）

實驗環境：emulator-5554，bot launched via dashboard `web_launch` API，CDP attach via 9230，
Cocos Creator 3.6.3 + protobuf-over-WebSocket。

## 結論先寫

**「直接透過 WebSocket 自動化頁面切換」可行但不建議。**
更乾淨的方式是 attach CDP 後直接對 cocos 節點呼叫 `emit('click', node)`，
這會觸發 cocos 自己的點擊處理，cocos 會把對應的 WS 封包打出去，
state 也同步更新。等於免費獲得 WS 的所有好處（無動畫、無 ROI 校正、無點擊失誤），
而且不用解 protobuf。

## 抓到的協議片段

### `0x4707` (TX) — 頁面/功能入口請求

Schema:
```proto
message NavigateReq {
  repeated int32 ids = 1;  // 要載入的 feature ids
}
```

實測 body：

| 動作 | body bytes | 解碼 (ids) |
|------|-----------|------------|
| 主頁 → 家園 tab | `08 00` | `[0]` |
| 家園 → 農場 (PlantMainView) | `08 e9 07 08 d1 0f 08 b9 17` | `[1001, 2001, 3001]` |

對應 RX 0x4707 回傳 149/459 bytes 的 feature 資料；
0x0c11 (605 bytes) 在家園情境額外觸發（懷疑是礦坑 inventory）。

### `0x0c11` (TX/RX) — 家園 mining/inventory bulk

進入家園時隨 0x4707 一起 fire；TX 空 body，RX 605 bytes。

### 關閉/返回操作 — **無 WS 封包**

| 動作 | 觀察 |
|------|------|
| `PlantMainView/bottom/btnClose` 關閉農場 | 0 個新 cmd，純客戶端 |
| Toggle off 家園 tab (再點一次) | 0 個新 cmd，純客戶端 |

關閉是純客戶端動作，UI 局部 deactivate。下次重新進入會重新發 0x4707 取資料。

## Cocos 節點路徑備忘

| 目標 | Cocos node path |
|------|-----------------|
| 家園 tab | `/UIRoot/NormalView/MainView/tab/scrollTab/view/content/4` |
| 農場 入口 (在家園頁) | `/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Farm` |
| 農場頁本體 | `/UIRoot/NormalView/PlantMainView` |
| 農場 關閉鈕 | `/UIRoot/NormalView/PlantMainView/bottom/btnClose` |
| Tab bar 索引 | content/1=角色, /2=同伴, /3=副本, /4=家園, /6=家族, /5=商店 |

## 推薦自動化模式

```python
# attach + emit click 模式
from playwright.sync_api import sync_playwright

CLICK_JS = """
  ([path]) => {
    const find = (root, parts) => {
      let n = root;
      for (const p of parts) {
        if (!n || !n.children) return null;
        n = n.children.find(c => (c.name || '') === p);
        if (!n) return null;
      }
      return n;
    };
    const node = find(cc.director.getScene(), path.split('/').filter(Boolean));
    if (!node) return 'not_found';
    node.emit && node.emit('click', node);
    return 'clicked';
  }
"""

def navigate(page, path):
    return page.evaluate(CLICK_JS, [path])

# 主頁 → 家園 → 農場
navigate(page, '/UIRoot/NormalView/MainView/tab/scrollTab/view/content/4')
# wait for cocos to load (settle 1.5~2s)
navigate(page, '/UIRoot/NormalView/MainView/container/MysteryMainView/MysteryMainView/items/Farm')
```

## 何時才該走純 WS

直接送 0x4707 protobuf 只在這些情境才值得：
- 需要批次操作（一次切 N 個頁面取資料）
- 想跳過 cocos 端的 cooldown / 動畫鎖
- 想做 stress test，模擬大量請求

注意：純 WS 切頁不會更新 cocos 端 UI，下次拍 screenshot 不會反映新狀態。
要 bot 用 screenshot+OCR 流程，必須讓 cocos 也切過去 — 走 emit('click') 是唯一乾淨解。

## 家園 (MysteryMainView) 全入口協議表

家園頁面下共有 10 個入口節點。下表是每個入口被點擊後抓到的非雜訊 WS cmd：

| 中文 | Cocos node | new view | tx 0x4707 ids | 其它 tx cmd |
|------|-----------|----------|---------------|-------------|
| 礦山 | `Mine` | MysteryMineView | `[4001]` | 0x0c21 `08 01` |
| 菇菇雕像 | `FarmStatue` | StatueView | — | 0x0c11 (empty) |
| 菇菇車位 | `CarPark` | ParkingMainView | `[7001]` | 0x0333, 0x3801×3 |
| 加工坊 | `WorkShop` | WorkShopView | — | 0x4802 (empty) |
| 比格先生 | `Marry` | MarryMainView/FavorView | — | 0x3801 id=15139 JSON `{"page":1}` |
| 農場 | `Farm` | PlantMainView | `[1001, 2001, 3001]` | — |
| 科技 | `Science` | ScienceView | `[5001]` | 0x1d1c `08 02`, 0x1d20 |
| 神秘商人 | `mysteryShop` | MysteryStoreView | — | 0x4903, 0x1b01 `08 17` |
| (inactive) | `Slave` | — | — | (該裝置未解鎖) |
| (容器) | `MarryScene` | — | — | (僅佈景，無入口) |

**0x4707 schema 推測**：`message NavReq { repeated int32 feature_ids = 1; }`。
傳入 ids 是要載入資料的 feature_id。並非所有頁都用 0x4707，雕像/加工坊/比格/神秘商人各自走獨立 cmd。

**0x3801 schema 推測**：`message GenericRpc { int32 op_id = 1; string json_body = 2; }`。Marry 用 op=15139 + `{"page":1}`；CarPark 用 op=12811 + `{}`。

抓包原始資料保存於 `tools/_probe_out/home_entries_<ts>/report.json`，包含每個入口的截圖、變化的 view names、所有 frames 的 hex preview。

## 關閉按鈕的優先順序

每個 overlay view 下可能有多種按鈕長得像「關閉」。實測結論：

1. **`btnClose`**: 真正的關閉按鈕 — 點了會 deactivate 整個 overlay
2. **`btnCancel`**: 確認對話框的取消鈕（如 `ParkingOneKeyReturnView`）— 也會關掉對話框
3. **`btnReturn`**: 功能性返回（如 ParkingMainView/top/scrollHorse/btnReturn 是「滾動列表回頂」）— **不會關 overlay**
4. **`btnBack`**: 視 view 而定，通常 OK

`utils/cocos_navigator.py` 的 `_FIND_CLOSE_BTN_JS` 已按上述優先序給分（btnClose=5, close=4, btnCancel=3, btnBack=2, back=1），並從**最頂層 overlay**（NormalView children 順序最後者）開始關。

## utils/cocos_navigator.py (production code)

`utils.cocos_navigator.CocosNavigator(page)` 提供 3 個方法：

| 方法 | 動作 | 純 client 還是會打 WS |
|------|------|----------------------|
| `goto_home()` | emit click 家園 tab | 打 WS (0x4707, 0x0c11) |
| `goto_farm()` | (若需要)先 goto_home → emit click Farm 節點 | 打 WS (0x4707) |
| `goto_main()` | 關閉所有 overlay → toggle 家園 tab | **純 client，不打 WS** |
| `current_view()` | 回傳 `"main"/"home"/"farm"/"unknown"` | 純 client |

外層用 `try_cocos_navigate(d, device_ip, target)` 包一層 flag gating：
- flag 未開（`experimental_cocos_navigation != true`）或非 web_h5 → 回 `None`
- 成功 → `True`
- 失敗 → `False`

caller (farm_v2、navigation.py) 看到 `None` 或 `False` 就 fallback 回原 click 流程。

## Page Detector — 狀態機

`utils/page_detector.py` 用兩階段識別當前頁面（不只是 main/home/farm）：

### PageState enum

涵蓋所有已知頁面：

| Enum | 用 cocos 哪個訊號識別 | OCR 後援關鍵字 |
|------|----------------------|---------------|
| `MAIN` | MainView active, MysteryMainView inactive, no overlay | — |
| `HOME` | MysteryMainView (inner) active, no overlay | 礦山+農場+加工坊 (2/3) |
| `FARM` | PlantMainView overlay | 種植 / 收成 |
| `MINE` | MysteryMineView overlay | 礦山+礦石 / 挖礦 |
| `STATUE` | StatueView overlay | 菇菇雕像 / 雕像祝福 |
| `CARPARK` | ParkingMainView overlay | 菇菇車位 / 馬廄 |
| `WORKSHOP` | WorkShopView overlay | 加工坊 / 合成 |
| `MARRY` | MarryMainView overlay | 比格先生 / 親密度 |
| `SCIENCE` | ScienceView overlay | 科技 / 研究中 |
| `MYSTERY_SHOP` | MysteryStoreView overlay | 神秘商人 / 神秘商店 |
| `EQUIP_EDIT` | EquipEditView overlay | — |
| `LOADING` | GameLoadingView 有 active 子節點 | 載入中 / loading |
| `GUIDE` | GuideView/GuideView (inner) active | — |
| `ROLE` | tab/1 selected, no overlay, no MysteryMainView | 角色+戰鬥力+升星 (2/3) |
| `PET` | tab/2 selected | 同伴+夥伴+出戰 (2/3) |
| `DUNGEON` | tab/3 selected | 副本+推圖+挑戰 (2/3) |
| `GUILD` | tab/6 selected | 家族+幫貢+宣戰 (2/3) |
| `SHOP` | tab/5 selected | 商店+限購+禮包 (2/3) |
| `UNKNOWN` | 不認識的 overlay 出現，或全部訊號都沒中 | — |

優先級：`LOADING` > `GUIDE` > 已知 overlay > `HOME` > 已選 tab > `MAIN`。
出現未知 overlay 一律回 `UNKNOWN`，**不會**默默被當成 MAIN（防 game patch 加新頁時自動化誤判）。

### 用法

```python
from utils.cocos_navigator import CocosNavigator
from utils.page_detector import PageState, PageDetector

nav = CocosNavigator(page)
nav.current_page()                       # cocos-only (fast)
nav.current_page(ocr_fallback=True)      # 允許 OCR 後援

det = PageDetector(page)
state, source = det.detect()             # source ∈ {"cocos", "ocr", "none"}
det.wait_for(PageState.HOME, timeout=5)  # 阻塞直到該狀態出現
```

### 何時 OCR 後援會啟動

- cocos 掃描失敗（page closed, cc 未載入）
- 出現未知 overlay → cocos 回 `UNKNOWN` → 不認識
- 載入畫面期間 cocos scene 尚未 bind

OCR 走 `img_tools.get_all_text(img)` → 比對 `PAGE_OCR_KEYWORDS`。
最具體的（min_matches 高）規則勝出；零規則 match 回 `None`。

### 接入 bot 主迴圈（new_main_v2.get_stage_with_check）

`utils.page_detector.try_detect_main_page_fast(d, ip)` 被 `new_main_v2.get_stage_with_check`
呼叫做為 fast-path：

- 回 `"主頁面"` ⇔ device 配置 `experimental_cocos_navigation=true` AND `backend=web_h5`
  AND device 有活著的 `_page` AND cocos 確認 `PageState.MAIN`
- 否則回 `None`，caller 走原本的 `resolve_stage_until_stable` OCR 路徑

設計重點：
- **只有 HTML (web_h5) backend 能用** — ADB 裝置不會碰這條路徑（沒 `_page` 屬性）。
- **非 MAIN 一律 fallback OCR** — 因為 `異地登錄/車位倉庫/家族戰/公告` 這類 popup
  cocos 偵測不到，必須走 OCR。Cocos 只負責「確認主頁」的高頻 case。
- 在 main page 時節省 ~1-3 秒/次（screenshot + OCR roundtrip）。
- 失敗安全：任何 exception → 回 None，bot 一定有 OCR 兜底，不會卡死。

Log 會印 `[ip] stage via cocos fast-path: 主頁面` 確認走 fast-path。

## Tests

| 檔案 | 模式 | Count |
|------|------|-------|
| `tests/test_cocos_navigator.py` | Mock Playwright page (CI-safe) | 25 |
| `tests/test_page_detector.py` | Mock + classifier + fast-path (CI-safe) | 39 |
| `tests/integration/test_cocos_navigator_live.py` | Real device via CDP 9230 | 11 |
| `tests/integration/test_page_detector_live.py` | Real device, overlays + fast-path | 15 |

Integration tests 自動 `skipif` CDP port 不在 listening — CI 機跑 `pytest` 不會炸。手動跑：

```bash
# Unit only (fast, no device needed)
pytest tests/test_cocos_navigator.py

# Real device (需 emulator-5554 web session 已開)
pytest tests/integration/test_cocos_navigator_live.py -v
```

## 工具參考

新增於本次研究：
- `utils/cocos_navigator.py` — production navigator
- `tools/probe_page_switch.py` — install/peek/drain/dump probe ring
- `tools/auto_click_and_capture.py` — emit click + baseline-vs-post diff
- `tools/analyze_home_entries.py` — sweep all home items + 截圖 + 報告
- `tools/enumerate_home_items.py` — 列出 MysteryMainView/items 子節點
- `tools/inspect_cocos_scene.py` — scene tree + netManager surface
- `tools/check_active_view.py` — current active prefab snapshot
- `tools/probe_view_state.py` — dump _VIEW_STATE_JS 結果（debug current_view 用）
- `tools/debug_goto_main.py` — 一步一步 trace goto_main
- `tools/find_farm_button.py` / `find_close_button.py` — keyword scene search
- `tools/snapshot_page.py` — Playwright screenshot via CDP

## See also

- `docs/protocol/REDPACK_SCHEMA.md` — 紅包協議 (cmd family 0x2601-0x2605, 0x0201) + `utils/redpack_detector.py` production API + 接入 `new_main_v2._run_redpack_check_if_due`。是用本文件的 cocos workflow 端到端完成的真實案例。

# 全部 OCR 盤點與非 ADB Cocos 遷移 TODO

狀態：規劃中

## Live 實測紀錄：7fe98fc6 / CDP 9226

2026-08-19 已用現有登入中的 H5 頁面做第一個安全切片驗證：

- 同一畫面存在兩層 view：底層 `ParkingWareHouseView`，上層
  `outlinePopView`（離線獎勵）。OCR 回傳「離線獎勵」，Cocos scene
  path 也確認上層為 `outlinePopView`，因此以最上層 Cocos view 作 stage
  判斷，避免誤先處理底層倉庫。
- 分支上的 `get_stage()` 以 Cocos 回傳 `放置獎勵`，fake screenshot
  會直接失敗，證明此已知狀態沒有進 screenshot/OCR。
- 直接呼叫同一個遷移 helper 的 node：
  `UIRoot/NormalView/outlinePopView/root/content/btnStart.emit('click')`。
  點擊前 node 為 active；3 秒後 `outlinePopView` inactive、
  `ParkingWareHouseView` 仍 active，與原本「領取離線獎勵後繼續處理下一層」的
  行為一致。
- `ParkingWareHouseView/root/content/rewardBtn` 也已確認 active 且有
  click listener；正式 handler 會在 Cocos 點擊失敗時有限重試，不回 OCR。
- 同一 live session 後續出現 `GoodsGetView`（標題圖像顯示「恭喜獲得」）；
  直接呼叫 `uiMgr.close('GoodsGetView')` 後 2 秒確認該 view inactive，
  `ParkingWareHouseView` 仍 active。此 popup 已納入下一個 Cocos stage adapter。
- 實際點擊 `ParkingWareHouseView/root/content/rewardBtn` 後約 0.8 秒，
  `ParkingWareHouseView=false`、`GoodsGetView=true`；呼叫 `uiMgr.close` 後約
  1.2 秒兩者皆 inactive，證明車位領取也必須等待並驗證獎勵 popup 關閉。
- 回到首頁後仍觀察到 `/UIRoot/TopView/NoticeView`、`imgMask` 與 `btnClose`
  active，而底層 `MainView` 也 active；因此公告必須是高於所有 `NormalView`
  page 的全域 `NOTICE` 狀態，不能只掃 `NormalView` 後誤判為主頁面。

這份紀錄只代表 7fe98fc6 的 live fingerprint；其他 H5 裝置仍需逐台確認
view 層級與點擊後 transition，不能直接複製成通用結論。

## 目標與邊界

本文件是目前專案中 OCR 的完整遷移清單。目標不是把 OCR server 刪掉，而是把使用場景分流：

- `backend=adb`：保留現有 OCR、影像辨識、座標與 EasyOCR/PaddleOCR 路徑，先確保功能不退化。
- `backend=web_h5`：正常流程全部改由 WebSocket、遊戲 runtime 或 Cocos scene/component 判斷與操作；不得因 Cocos 找不到節點就回退到 OCR。
- WebSocket 是遊戲資料的第一優先來源，Cocos 是 UI 狀態與 UI 操作的第一優先來源。兩者不是互相替代的 OCR，而是非 OCR 的資料來源。
- Cocos/runtime 無法取得可信狀態時，使用「有限重試 → 回到安全頁/重新排程/停用本次任務」；不可使用截圖 OCR 兜底。
- 中控的 OCR 健康檢查可以保留，但必須與遊戲任務的 OCR 使用量分開，不得讓診斷請求混入裝置 OCR 計數。

## 完成定義

- [ ] 所有 `web_h5` 正常任務的 action trace 都能標示 `ws`、`runtime`、`cocos` 或 `unavailable`，不再出現 `ocr_fallback`。
- [ ] `web_h5` 不呼叫 `img_tools` 的 OCR API、不呼叫 EasyOCR `readtext`、不呼叫 PaddleOCR `predict`，也不以 OCR 驗證截圖文字。
- [ ] Cocos 讀取有可驗證證據：scene/component/node、runtime state、封包資料或成功後的狀態變化；不能只因 `_page` 存在就宣稱成功。
- [ ] Cocos 失敗有明確 `reason`、`retryable` 與任務結果；不會靜默執行錯誤點擊。
- [ ] `adb` 仍能沿用既有 OCR 路徑；ADB 的 OCR 次數、失敗重試與 fallback 行為不在本次移除範圍。
- [ ] 所有殘留 OCR 呼叫都能歸類為：ADB 執行路徑、診斷工具、訓練工具、歷史程式，或尚未完成的遷移項目。

## 目標架構

### 後端分流

所有共用操作先經過 backend dispatcher，不在各任務內寫「Cocos 失敗就舊 OCR」：

```text
WS authority
    ├─ 成功：回傳 source=ws
    └─ 未覆蓋/失敗
        ├─ web_h5：Cocos/runtime → 成功 source=cocos/runtime
        │           失敗 → bounded retry / safe stop / requeue
        └─ adb：既有 OCR/影像/座標流程 → source=adb_ocr
```

建議統一回傳結構（名稱可依現有型別調整）：

```python
OperationResult(
    ok=True,
    source="cocos",          # ws / runtime / cocos / adb_ocr / unavailable
    view="FarmMainView",
    action="harvest",
    evidence={"node": "HarvestButton", "state": "READY"},
    reason=None,
    retryable=False,
)
```

### 共用層 TODO

- [ ] 擴充 `utils/cocos_ui.py`：統一 snapshot、component/node 查找、點擊後狀態驗證、timeout、scene fingerprint 與版本資訊。
- [ ] 擴充 `utils/cocos_view.py`：建立 feature view adapter，不讓各任務自行拼接 `page.evaluate()` 字串。
- [ ] 擴充 `utils/cocos_navigator.py`：首頁、返回、彈窗、任務入口與 loading 的可驗證 transition。
- [ ] 擴充 `utils/page_detector.py`：回傳完整 `PageState`；對 `web_h5` 未知頁面回傳 `unavailable`，不可再呼叫 OCR 猜頁面。
- [ ] 將 `experimental_cocos_navigation` 從必要開關改為能力/版本探測結果；完成 live fingerprint 後，所有 `web_h5` 都走同一個 strict Cocos dispatcher。
- [ ] 在 `img_tools.py` 增加 backend guard：當目前裝置是 `web_h5` 時，任何 OCR API 都記錄錯誤並拒絕執行；ADB 才允許進入 OCR endpoint。
- [ ] 對每次辨識與操作加上 `device_id`、`task`、`source`、`view`、`reason`、`attempt`；把 `ocr_request` 與 `ocr_diagnostic_request` 分開計數。
- [ ] Playwright page 操作保持在該裝置自己的執行緒/事件迴圈；不可為了共用 Cocos helper 而跨執行緒使用 page。
- [ ] 為所有 adapter 提供 `CocosUnavailable` 的明確結果，不以 `None`、空字串或 `False` 混淆「未找到」「尚未載入」「判斷為否」。

## 一、OCR 核心與共用 wrapper 全清單

這些是所有任務最終可能共用的入口，必須先完成分流，否則單點遷移仍會從 wrapper 間接打到 OCR。

| 檔案 | 入口/範圍 | 非 ADB 處理 | ADB 處理 |
|---|---|---|---|
| `img_tools.py` | `_call_ocr_endpoint`、`get_all_text`、`get_all_text_with_results`、`analyze_skill_via_http`、`analyze_stage_via_server` | [ ] 禁止呼叫；改由 dispatcher 擋下並回傳 `unavailable` | [ ] 原樣保留，維持 server priority、retry、usage 記錄 |
| `img_tools.py` | `click_str_by_server`、`check_str_in_region`、`wait_for_any_text` | [ ] 改成 Cocos node/component/state API；不能把 screenshot 傳給 OCR | [ ] 保留既有 OCR click/wait |
| `Open_gold_paddle_ocr.py` | `get_all_text`、`analyze_skill_via_http`、`analyze_stage_via_server` wrapper | [ ] 明確標示 legacy/ADB-only，避免 web_h5 import 後被呼叫 | [ ] 保留既有相容介面 |
| `Open_gold_paddle_ocr.py` | `extract_ocr_results`、`ocr.predict` | [ ] 禁止在 web_h5 路徑初始化或執行 | [ ] 保留給本機 PaddleOCR/ADB |
| `ocr_server.py` | PaddleOCR server | [ ] 不作遊戲 UI 判斷來源 | [ ] 保留為 ADB OCR service |
| `game_state/detector.py` | `get_stage`、`analyze_skill_via_http` legacy reader 參數 | [ ] stage 改用 `PageState`/Cocos/runtime；移除 web_h5 OCR branch | [ ] 保留 ADB stage OCR |
| `game_initialization.py` | `resolve_stage_until_stable`、車位倉庫 OCR click | [ ] 初始化、popup、車位入口改 Cocos transition | [ ] 保留 ADB OCR |
| `game_actions/stage_guard.py` | `get_stage_with_check` | [ ] Cocos page state 是 web_h5 唯一 UI 判斷來源 | [ ] OCR stage guard 保留 |
| `utils/page_detector.py` | `_ocr_to_text_list`、`detect_known_h5_page` | [ ] web_h5 不進 `_ocr_to_text_list`，未知頁安全退出 | [ ] ADB 可使用舊 detector |
| `control_panel/routes_status.py` | OCR health/stage diagnostic | [ ] 改讀 worker 回報的 Cocos/runtime 狀態；OCR 測試改為明確的「ADB/OCR diagnostic」按鈕 | [ ] 保留 OCR diagnostics，但不混入 bot usage |

## 二、主流程、農場、任務與獎勵

| 檔案 | OCR 現況 | Cocos/runtime 遷移內容 |
|---|---|---|
| `farm_v2/manager.py` | `wait_for_any_text` 等待農場文字 | [ ] 由 farm view state、loading state、popup node 取代；無法確認就重試/安全返回 |
| `farm_v2/operations/harvest_card.py` | 工作面板、導航、車位/商店、種子、肥料、收穫與 popup 多處 OCR | [ ] 完成 `FarmMainView`、work panel、seed/fertilizer、harvest result、carpark/shop 的 Cocos adapter；每次點擊都驗證 view/state transition |
| `game_actions/navigation.py` | `click_str_by_server("關閉")` | [ ] 通用 close/back 改 Cocos popup stack；ADB 分支維持 OCR |
| `game_actions/reward_manager.py` | `wait_for_any_text` 等獎勵 popup | [ ] 讀 reward component、claim button node 與 popup state；不以文字存在作唯一依據 |
| `game_actions/miner_action.py` | 確認 popup `click_str_by_server("確定")` | [ ] Cocos dialog button + dialog closed transition |
| `Mission.py` | 任務領取 `analyze_skill_via_http` | [ ] 任務 panel、任務完成狀態、claim node、領取後計數改由 Cocos/runtime |
| `daily_gift_task.py` | 好友/伴侶頁面大量 `click_str_by_server`、`wait_for_any_text` | [ ] `MarryMainView`、partner list、gift count、send/claim button 與完成狀態全部由 Cocos/WS 取得 |
| `Spin_Wheel.py` | 郵件、好友、獎勵、轉盤結果多處 OCR | [ ] mail/friend/turntable view adapter；用 reward state 或轉盤 result component 判斷完成 |
| `game_actions/periodic_tasks.py` | 功夫任務兩處 OCR | [ ] 功夫入口、挑戰按鈕、結果 popup 由 Cocos node/state 操作 |
| `week_events.py` | 功夫活動文字搜尋 | [ ] 與 periodic tasks 共用 event view adapter |
| `game_actions/skill_manager.py` | 技能、確定/關閉與結果判斷 | [ ] 技能面板、可用技能、確認與施放結果改為 Cocos/runtime；不再依賴 web_h5 OCR |
| `game_actions/statue_weekly.py` | 雕像週任務多個 OCR 判斷/點擊 | [ ] 雕像 view、進度、挑戰、獎勵 popup 做成 Cocos adapter；ADB 仍走現有流程 |
| `game_actions/reward_manager.py`、`Mission.py` | 共用 reward/task popup 可能重複掃描 | [ ] 共用一次 snapshot/transition，避免任務層各自觸發辨識 |

## 三、戰鬥、競技場、家族與商店

| 檔案 | OCR 現況 | Cocos/runtime 遷移內容 |
|---|---|---|
| `game_actions/arena_battle.py` | 入口、動畫、結果、結束仍有 OCR fallback | [ ] 擴充 `CocosArena` 覆蓋 enter、battle state、result、finish、return home；web_h5 失敗只安全退出/重排，不進 OCR |
| `battle/cloud.py` | `CocosCloudBattle` 之外的入口、確認、結果與返回大量 OCR | [ ] 讓 `CocosCloudBattle` 覆蓋整個流程；統一 popup/result adapter |
| `game_actions/cocos_cloud_battle.py` | 已有 Cocos path，但呼叫端仍可 fallback | [ ] 移除 web_h5 fallback；增加 node evidence 與狀態驗證 |
| `battle/_helpers.py` | 關閉/確認共用 OCR helper | [ ] backend-aware close/confirm；web_h5 只呼叫 Cocos dialog helper |
| `family.py` | 家族入口、活動、聊天室/任務、冰雪國等 OCR/座標 | [ ] `CocosFamily` 覆蓋入口、活動、任務、聊天與 popup；家族協定資料仍優先走 WS |
| `game_actions/cocos_family.py` | 已有部分 Cocos helper | [ ] 補齊未覆蓋 view，且將「節點不存在」轉成可觀測 unavailable，不得回 OCR |
| `battle/special.py` | 冰雪國/特殊戰鬥 OCR fallback | [ ] H5 使用 Cocos family/event driver；ADB 保留 `fight_snow_country` OCR |
| `battle/weekly_trials.py` | ADB 回合 OCR；H5 有 `rogue_h5` Cocos 戰鬥，但周邊流程仍可能 OCR | [ ] 將商店、清場、返回首頁、獎勵 popup 分成 Cocos/ADB 兩條；`_fight_rounds_ocr` 僅允許 ADB |
| `battle/rogue_h5.py` | H5 Cocos 戰鬥 | [ ] 補足進入/結算/離場證據，確認整條 H5 weekly path 不會呼叫 OCR |
| `battle/store.py` | `buy_god_everyweek` 使用 OCR | [ ] 商店商品、價格、購買結果、確認 popup 改讀 Cocos component/runtime；ADB 保留 |
| `BUY.py` | 商品/狀態 `analyze_skill` | [ ] H5 導向 Cocos shop adapter；無法確認商品不可購買，不得猜點 |
| `Store.py` | `gather_detections`、購買流程遠端 OCR wrapper；`__main__` 還初始化 EasyOCR | [ ] 生產 H5 走 Cocos shop；移除生產 import 的 EasyOCR 初始化，保留 ADB/legacy entry 明確標籤 |
| `rank_events.py` | 排行/坐騎/車位相關頁面 OCR | [ ] 由 event/park Cocos state 取代；確認實際生產入口後刪除未使用舊分支 |

## 四、挖礦與海域

| 檔案 | OCR 現況 | Cocos/runtime 遷移內容 |
|---|---|---|
| `miner/core/ocr_utils.py` | 鎬、鑽頭、炸彈等 OCR；web 目前部分優先 WS | [ ] web_h5 只接受 WS inventory 或 Cocos inventory component；缺資料時 unavailable/重排；ADB OCR 保留 |
| `miner/mining_service.py` | overlay ROI OCR、定期鎬數量 OCR、錯誤 overlay 判斷 | [ ] overlay 開關/關閉由 Cocos scene node，物品數由 WS/runtime inventory；移除 `_PICKAXE_OCR_VALIDATE_EVERY` 的 web_h5 分支，保留 ADB 驗證 |
| `miner/mining_service.py` | `overlay_ocr_calls` telemetry | [ ] 改成 `overlay_state_checks`，並保留 OCR counter 只統計 ADB；避免「沒有 page」與「OCR 成功」混為一談 |
| `miner/scripts/Mining_等待改進.py` | 舊腳本 OCR | [ ] 標示 historical/non-runtime；若仍有入口，改成 Cocos/WS 或移出啟動範圍 |
| `sea_v2/session.py` | 資源格、駐軍/攻擊/出航/行軍/獎勵多處 `analyze_skill_via_http` 與 OCR tap | [ ] 建立海域 scene adapter，讀 tile、menu、garrison、attack、start voyage、marching、reward component；按鈕點擊後驗證狀態 |
| `sea_v2/rewards.py` | task panel、獎勵 popup 的 `get_all_text`/`analyze_skill` | [ ] milestone node 之外補 task/reward component；WS 能判斷的 claim 優先 WS，UI 只用 Cocos |
| `Sea.py` | 舊海域流程多處 OCR | [ ] 確認所有生產入口改到 `sea_v2`；若仍有 ADB 入口保留，非 ADB 禁止使用此舊流程 |

## 五、開神燈、停車場與其他舊模組

| 檔案 | OCR 現況 | Cocos/runtime 遷移內容 |
|---|---|---|
| `opengold_v2/ui_controller.py` | skill/stage OCR、文字點擊、全文 OCR | [ ] H5 改讀 `lamp_ui_state`/Cocos component；按鈕用 node id；ADB 保留 wrapper |
| `opengold_v2/lamp_service.py` | Cocos/封包已有路徑，但多候選與封包缺資料時回 OCR | [ ] web_h5 改為 packet + Cocos state only；歧義時停止本次升級並重新排程，不得 OCR；明確記錄 `ambiguous_lamp_state` |
| `new_park.py` | 坐騎/停車場 `analyze_skill`、`click_str` | [ ] web_h5 改 Cocos park/garage view；確認主流程是否仍引用，未引用則移至 legacy |
| `fight_car.py` | 車位戰鬥與 OCR；並 import EasyOCR | [ ] H5 使用 WS/Cocos carpark state；ADB 保留；移除 web_h5 初始化 EasyOCR |
| `park.py` | EasyOCR/舊停車場流程 | [ ] 標示 ADB-only 或 historical；不可由 web_h5 生產流程 import/呼叫 |
| `gold_mananer.py` | 舊 EasyOCR reader | [ ] 確認是否仍被入口使用；若非 ADB 入口，改 Cocos/WS 或封存 |
| `oracle_manager.py` | 舊 EasyOCR `readtext` | [ ] 目前舊挖礦 reader 若無生產呼叫，移除非必要 import；若仍有 ADB 呼叫，標示 ADB-only |
| `battle/manager.py` | `self.reader.readtext` 舊 BattleManager | [ ] 追蹤實際 instantiate call site；web_h5 禁止使用，ADB 是否保留另以測試確認 |

## 六、EasyOCR、PaddleOCR 與直接辨識殘留

下列不一定都是目前生產路徑，但都要有明確處置，避免未來被重新接回 web_h5：

- [ ] `battle/manager.py`：追蹤 EasyOCR reader 是否仍被建立/使用。
- [ ] `fight_car.py`、`park.py`、`gold_mananer.py`、`oracle_manager.py`、`Store.py`：移除非 ADB 路徑的 EasyOCR import/初始化；ADB 路徑加註 backend guard。
- [ ] `utils/wake_up_handler.py`、`game_state/detector.py`：清理只剩參數名稱的 `easyocr_reader` 相容參數，避免被誤認為仍有 web OCR。
- [ ] `Open_gold_paddle_ocr.py`、`ocr_server.py`：保留 ADB/診斷用途，禁止由 web_h5 task dispatcher 呼叫。
- [ ] `OCR/test.py`：訓練/實驗工具不納入 bot runtime；在掃描器中排除並加說明。
- [ ] `easyocr_calls.log`：這是歷史靜態掃描結果，不是即時 OCR 計數；更新掃描範圍與分類，排除 `trash`、歷史資料、訓練資料、scanner 自身。

## 七、診斷、沙盒與歷史程式

- [ ] `control_panel/routes_status.py` 的 OCR endpoint 改名或加上 `diagnostic` 標記，前端顯示「僅 ADB/OCR 測試」，不可當成 H5 stage 判斷。
- [ ] `task_sandbox/navigator.py` 的 OCR 只允許在明確的 ADB/debug profile；若 sandbox 要驗證 H5，改用 Cocos snapshot。
- [ ] `miner/scripts/Mining_等待改進.py`、`Sea.py`、`new_park.py` 等舊入口先做 call graph 追蹤，再決定遷移或封存，不要只刪除函式造成隱性 import error。
- [ ] 所有文件、註解、測試名稱把「OCR fallback」改成「ADB OCR」或「Cocos unavailable safe stop」，避免後續開發者重新加回 web_h5 fallback。

## 八、建議實作順序

### P0：先建立硬性邊界與可觀測性

- [ ] 建立 backend-aware `OperationResult`/錯誤碼。
- [ ] `img_tools` 增加 web_h5 OCR guard。
- [ ] 統一 action trace 與 OCR usage 的 source/task/device 欄位。
- [ ] 為 web_h5 加測試：任何 OCR API 被呼叫都必須 fail fast；ADB 呼叫仍成功。
- [ ] 盤點 `experimental_cocos_navigation` 與 `use_ws_runner` 的實際生效裝置，建立 capability matrix。

### P1：頁面辨識、啟動、導航、彈窗

- [ ] 完成 `PageState`/scene fingerprint。
- [ ] 啟動恢復、首頁、返回、loading、close/confirm、reward popup 全部改 strict Cocos。
- [ ] 取消 `page_detector` 對 web_h5 的 OCR fallback。
- [ ] 用 live H5 逐台建立 node/component fingerprint，涵蓋 5554、5556、5558、5560、7fe98fc6、web-001～004。

### P2：農場與日常任務

- [ ] 農場收穫、工作面板、種子、肥料、車位/商店。
- [ ] 任務、每日禮物、好友/伴侶、郵件、轉盤、技能、功夫、雕像與獎勵。
- [ ] 每個任務移除「H5 Cocos 失敗 → OCR」分支，改為 unavailable 結果。

### P3：戰鬥與活動

- [ ] 競技場完整結果流程。
- [ ] 雲端戰鬥完整入口/戰鬥/結算。
- [ ] 家族、特殊戰鬥、萬神試煉周邊、家族商店/一般商店。
- [ ] 每個 Cocos adapter 補點擊後 transition assertion。

### P4：挖礦、海域、神燈

- [ ] 挖礦 overlay、物品數量、盤面與錯誤提示改 WS/Cocos。
- [ ] 海域 tile/menu/駐軍/攻擊/出航/獎勵完成 Cocos adapter。
- [ ] 神燈屬性、候選、升級、封包不完整情況改安全停止，不 OCR。

### P5：清理與防回歸

- [ ] 移除 web_h5 路徑的 EasyOCR/PaddleOCR import、初始化與相容 fallback。
- [ ] 把未使用舊模組標示 ADB-only/historical，從生產入口隔離。
- [ ] 更新 OCR 靜態掃描器規則，只把真正 runtime 的 ADB OCR 列入清單。
- [ ] CI/測試加入 `web_h5 + OCR API` 禁止規則與 source assertion。

## 九、測試與驗收清單

### 靜態檢查

- [ ] 掃描 `img_tools.get_all_text`、`get_all_text_with_results`、`analyze_skill_via_http`、`analyze_stage_via_server`、`click_str_by_server`、`check_str_in_region`、`wait_for_any_text` 的所有 production caller。
- [ ] 掃描 `.readtext(`、`ocr.predict(`、`easyocr.Reader`、`PaddleOCR(` 的所有 production caller。
- [ ] 每一個結果標註 ADB-only、diagnostic、training、historical 或待遷移；不允許未分類。
- [ ] `git diff --check` 與 UTF-8/BOM 檢查通過。

### 單元/整合測試

- [ ] Cocos node 存在、node 不存在、scene 尚未載入、timeout、版本 fingerprint 不符。
- [ ] web_h5 Cocos 成功：source 為 `cocos`/`runtime`，OCR counter 不增加。
- [ ] web_h5 Cocos 失敗：結果為 `unavailable`，有 reason，執行 bounded retry/safe stop，OCR counter 不增加。
- [ ] ADB：既有 OCR server fallback、retry、usage counter 與點擊流程維持。
- [ ] WS 成功：source 為 `ws`，不額外開 UI 或 OCR。
- [ ] 多裝置並行：每個 page、trace、OCR counter 不串台。

### Live H5 驗收

- [ ] 每台 web_h5 裝置各跑一次啟動/首頁恢復。
- [ ] 各跑一次農場、任務、獎勵、戰鬥、商店、挖礦、海域、神燈代表流程。
- [ ] 檢查 logs/action trace：web_h5 `ocr_request=0`；若有 diagnostic request 必須獨立標示。
- [ ] 人為阻斷 Cocos snapshot 或改變 node fingerprint，確認系統安全停止/重排，而不是偷偷 OCR。
- [ ] ADB 裝置重跑原有目標測試，確認 OCR 行為未被 global guard 誤傷。

## 十、交付規則

- [ ] 共用 helper 先獨立提交，再按 domain 分支接入；避免多個任務同時修改 `utils/page_detector.py`、`utils/cocos_ui.py`、`utils/cocos_navigator.py`。
- [ ] 每一個 domain 的提交都要附：移除的 OCR caller、Cocos evidence、失敗策略、ADB 回歸結果。
- [ ] 不以「截圖看起來成功」作為完成條件；必須有 runtime/Cocos state transition 或 WS evidence。
- [ ] 所有 Cocos 遷移完成前，保留 ADB OCR 實作；最後才清理非 ADB import 與歷史 wrapper。

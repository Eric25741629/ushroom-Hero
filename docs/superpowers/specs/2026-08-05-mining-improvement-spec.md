# 挖礦系統全面觀察與改進規格 (Mining Improvement Spec)

- 日期: 2026-08-05
- 範圍: `miner/` 全套挖礦程式碼之靜態分析、觀察點整理、改進規格
- 目的: 供後續新 session 依此規格逐項改進;每項含「現象/位置、影響、建議改法、驗證方式、合理性」
- 版本: v1
- 狀態: 草稿（尚未開始實施）

---

## 1. 現況盤點（分析時的事實基礎）

### 1.1 架構

```
主迴圈 (miner/mining_service.py run(), L560-927)
  截圖 (shared_frame) → CNN 分類 7x6 盤面 (miner/models/classifier.py)
  → DepthTracker 對齊捲動 (miner/depth_tracker.py)
  → 庫存刷新 (OCR 或 WS 0x0401) → _dispatch_planner 選版規劃
  → shadow 額外計算 (miner/mining_service.py:350)
  → 預檢 → execute_plan_steps 執行 (miner/planning/executor.py)
  → ExecutionResult 回帳 (鏟子/道具扣減) → 迴圈
```

- 畫面: 7 列 x 6 格 viewport（`core/config.py::GRID_CFG`）
- CNN 標籤: 10 種（`core/config.py::DEFAULT_CLASSES`）
- web_h5: 額外用 WS 0x0c01 重建視窗下方已知地形（`mining_service.py:752` `read_ws_below_rows`），可規劃到約 21 列
- 兩條執行資料流:
  - WS/online: `ws_token/mining_adapter.py` 監督迴圈,每步重規劃、只執行第一步,`exec_profile="step"`(mining_adapter.py:749)
  - CNN/ADB: `mining_service.py` 整批執行完整路徑,`exec_profile="plan"`(預設)

### 1.2 Planner 生態系

| 版本 | 模組 | 策略 | 現況 |
|------|------|------|------|
| v1 | `miner/planning/smart_planner.py` | A*,目標「收完礦+打通底層」,道具影子價 2.99 | code 預設值 |
| v2 | `miner/planning/planner.py` | 貪婪單步 | 2026-06-05 移除,檔案保留工具函式 |
| v3 | `miner/v3/planner.py` + `board.py` + `actions.py` | cluster-aware | 可用 |
| v4 | `miner/v4/planner.py` | bounded 3-step DFS | 可用 |
| final_v1 | `miner/final_v1/planner.py` + `scoring.py` + `types.py` | bounded beam search + 位能場 | 主力開發對象,Dashboard 可切換 |

設定現況:
- `config_manager.py:1260` 白名單已含 `{"v1","v3","v4","final_v1"}`(已完成 spec 要求的接線)
- `bot_config.json`: 幾乎全部裝置 `mining_planner_version="final_v1"`;只有一台(line 1779,web 測試裝置)維持 `v1`
- shadow: 全部為 `""`(未啟用) — 即 final_v1 已全面當主 planner,但沒有任何對照組

### 1.3 命名陷阱（已確認）

`mining_service.py:28-33` 從 `miner.planning.planner` import `base_label/enter_cost/is_empty`(那是 v2 的工具函式),而 v1 A* 真正在 `smart_planner.py`。`planning/planner.py` 檔名是 v2 內容,`plan_smart` 才是 v1。「v1」在程式碼裡指 `plan_smart`(A*)。任何接手者極易誤導。

---

## 2. 觀察點與改進規格

> 每項標記優先級: 🟥 P0(直接影響產出/正確性) 🟧 P1(效率與一致性) 🟨 P2(可維護性/工具性)
> 「驗證方式」欄是後續 session 的驗收判據。

---

### P0-01 強制下挖 (forced descent) 繞過 planner,可能白吃鏟子

**現象/位置**: `mining_service.py:497-513` `_forced_descent_dig` + `mining_service.py:797-819`。當 plan 空但 `remaining_pits>0`(被黑名單/封閉口袋困住)時,直接挑**最深可挖的非 pit frontier** 下挖推進捲動,不經 planner、無收益評估。

**影響**:
- 選中格只需「最深」,不保證挖完後 row7 出現可達空氣(即不保證真的捲動)。若挖了但未觸發捲動,流程回到主迴圈,鏟子被消耗而無推進,接著 empty plan 計數繼續累積,最後仍中止。
- 與 final_v1 的 `_pull_progress`/位能場邏輯完全脫鉤,容易選到低價值格。

**建議改法**:
1. 首選: 把 descene 決策交回 planner。給 `plan_final_v1` 一個 `force_descent: bool` 旗標,由它用既有位能場選「最低成本、最可能打通 row7」的格,回傳單步 dig plan。
2. 次要: 保留現有 heuristics 但加強判據,條件改為「該格 dig 後 `floor7_open` 預測成立」:優先挑 row6 可挖格,且同 r 時挑 dig_cost 最小者 (rock 2 鏟高成本不該優先)。同時在 `floor7_fully_clear` 語意上選「能讓整列出現可達空氣」的格(見 `v3/board.py::floor7_fully_clear`)。
3. 驗證方式: 用 `miner/scripts/simulator.py` 或新增測試,建「最後一列全 rock、row5 有 dirt」的盤面;斷言 descent 選到的是會讓捲動發生的格;並量測 descent 成功率(發生捲動比例)進 telemetry(`terminated_reason` 旁加 `descent_scrolled` 欄位)。

**合理性**: 保留應急路徑是合理的；但「不保證捲動」目前尚未被證實。executor 對 row6 有明確的下樓分支，因此應先以 replay/telemetry 測量實際捲動成功率，再決定是否升級為 planner 內建的 force-descent。現階段較確定的問題是沒有成本/收益 tie-break，可能選到昂貴 rock。

---

### P0-02 final_v1 fallback 標記不實,污染 KPI 統計

**現象/位置**: `mining_service.py:398-419`。final_v1 出錯或空 plan 時 fallback 到 v1,但 `plan["planner_name"]="final_v1"`、`planner_source="v1_fallback"`;log 標題「Final V1 規劃 (v1 fallback)」。

**影響**: 任何以 `planner_name` 彙整的 log/KPI 統計(MiningMapRecorder、telemetry、dashboard)會把實際由 v1 執行的輪次計入 final_v1,美化/混淆產出歸因。`print_plan_result` 等顯示也會誤導除錯者。

**建議改法**:
1. `planner_name` 應反映**真實執行的規劃器**(`v1`);真實身份放 `planner_source="final_v1_fallback"`(或 `origin` 欄位)。
2. 統計時若有需要「final_v1 的覆蓋面」,用 `planner_source` 單獨歸類,而非 `planner_name`。
3. 驗證方式: 單元測試 `_dispatch_planner`(mock `plan_final_v1` 拋錯),斷言回傳 `planner_name=="v1"` 且 `planner_source=="final_v1_fallback"`。

---

### P0-03 道具執行失敗即整煞黑名單,可能誤殺

**現象/位置**: `executor.py` NoBoardChangeError(道具)與 `mining_service.py:849-855`:道具使用後版面無可歸因變化 → `item_blacklist.add(item_type)` 直到本次 session 結束。

**影響**:
- 若失敗其實是「驗證視窗錯過變化」(例如動畫太長、WS 0x0c01 盤面更新延遲),同一道具會被整場禁用,損失該 session 的所有高價值道具機會。
- WS 驗證目前已把 0x0401 庫存下降視為成功確認(executor L350-356);因此「盤面無變化 + 庫存有下降」主要是延遲、讀取失敗或非 WS 路徑的風險，不能直接視為現行 WS 必然誤殺。

**建議改法**:
1. 保留現有 WS inventory delta 成功判定，補測試覆蓋「盤面未更新但庫存下降」以及「庫存讀取暫時失敗」兩種情境。
2. 對非 WS 或連續驗證失敗的情況，再評估使用次數門檻:同 item 連續 fail N 次(建議 2)才黑名單,單次失敗只加 blocked_action。
3. 驗證方式: 用 WS adapter 測試或 fake 建「盤面不變但 0x0401 庫存-1」情境,斷言 `OutOfItemError`/黑名單不誤觸發。

---

### P0-04 主迴圈無全域例外保險

**現象/位置**: `mining_service.py::run()` 主 while 迴圈無 try/except 包圍;`_dispatch_planner` 有 fallback,executor 有例外型別,但 executor/RNN/OCR 未預期的例外(如 u2 連線斷線、CNN 推論炸掉)會直接冒出,中止該裝置挖礦執行緒。

**影響**: 一台裝置的好時段可能因單次偶發例外提早結束挖礦,損失整段產出。`bot_state`/device_runtime 是否兜底屬未知(不在本次分析範圍)。

**建議改法**:
1. `run()` 內包一層 `try/except Exception`,記 log + map_recorder 標 `fatal_error`,然後 break 乾淨離開(仍執行 `map_recorder.end()` 與 `rl_recorder.flush()` — 注意這兩者在 L921-925 是在迴圈外,必須在 exception 路徑也確保執行)。
2. 對已知可自癒的例外(連接暫時失敗)做 1-2 次重試(延時 3-5s 後重截圖)再放棄。
3. 驗證方式: 用 fake device 的 `screenshot` 在 N 次後拋例外,斷言 run() 正常返回、recorder 有 call end/flush、無未處理例外浮出。

---

### P1-05 7 列 CNN 盤面炸彈底列收益被低估,ADB 與 WS 行為不一致

**現象/位置**: `final_v1/planner.py::_affected`(L123-131) + `v3/actions.py::get_bomb_targets`。炸彈 footprint 以 `rows,cols` clamp 進 board;7 列板底列(row6)放炸彈時,畫面外爆炸格不在板內 → 完全不計收益。而 21 列 WS 板可計已知畫面外。spec(`2026-07-11-final-v1-mining-planner-design.md`)明言「炸彈可計算已知畫面外收益」,但 CNN/ADB 路徑其實拿不到,形成兩後端行為分叉。

**影響**:
- 底列炸彈的價值在 CNN/ADB 被系統性低估,planner 傾向不炸底列,可能錯過高價值連鎖(尤其配合未知底部 3x2 收益,`item_planner` 的 `collect_bottom_triplets` 有估過這種收益)。
- 兩後端 KPI 無法直接對比(A/B 比較被後端差距污染)。

**建議改法**:
1. 短期: 文件化+telemetry 標註 `shadow_below_rows_count` / `board_known_rows`,讓 A/B 分析知道視野差異。
2. 中期: 讓 CNN/ADB 也能擴充下方已知地形:
   a. 若 `mining_map_record` 有累積 global_map(`utils/mining_map_recorder.py`),在捲動後可對齊給出下方少量已知列(與 depth_tracker 相同對齊法)。
   b. 或當 WS token 與 CNN 同時存在(5554 案例),直接 `read_ws_below_rows`(目前該呼叫對 adb 回 [] — 檢查是否因 backend 判斷而跳過,是則放寬)。
3. 驗證方式: 新增測試: 7 列板、row6 放炸彈 vs row4 放炸彈,斷言「兩者的計入收益差異」被明確建模(可測試: 現在 row6 低於 row4 的拍後收益),再優化策略。
4. 註: 炸彈「已知畫面外」下限為 0 是目前最安全假設,不要改成猜測收益(違反 non-goal「不猜測未知」)。

---

### P1-06 shadow 對照組設計不足,無法量化 final_v1 相對 v1 的改善

**現象/位置**: `_compute_shadow_plan`(mining_service.py:350-383)只回 `first_step` + `score_breakdown`;`mining_shadow_planner_version` 目前全裝置為空字串(bot_config 全 `""`);且 mining_service 側 shadow 恆用 `exec_profile="plan"`,與 WS 的 `"step"` 行為分叉。

**影響**:
- 無對照組: 所有裝置都是 final_v1,BVT 無法回答「final_v1 到底比 v1 好多少」。
- shadow 回的資料不足以做同構比較: v1 出整批 plan,shadow 只回第一步,產出/效率無從對齊;`score_breakdown` 的評分模型與 v1 完全不同,不能直接比 score。
- 在 `miner/mining_service.py` 中 shadow 固定走 `exec_profile="plan"`,只覆蓋 CNN/ADB 的 plan 面；WS adapter 已使用 `exec_profile="step"`，不能把兩個 backend 概括成同一問題。

**建議改法**:
1. 至少保留 1-2 台專職對照裝置: `mining_planner_version="v1"` + `mining_shadow_planner_version="final_v1"`,並長期跑,讓 shadow 每輪可對比。這是低成本、不改程式就能做的改善。
2. shadow 輸出升級為與主 planner 同構: 回傳 `preview_steps`(完整路徑) + `predicted_result`(虛擬執行該路徑的 `(pits_collected, shovels_used, items_used, scrolled)`),用 `scoring.py` 的 `evaluate_state` 統一結算;與實際執行回帳的 ExecutionResult 直接對齊比對。
3. shadow 增加 `exec_profile` 參數穿透(現在 shadow 無 profile 參數,一律 plan)。
4. 驗證方式:
   - 離線 replay(`tools/replay_real_boards.py`)同一批真實 board: v1 vs final_v1,輸出 `(pits, shovels, items, pits/(shovel+3*items))`,兩邊都用**真的 plan→simulate** 而非 raw planner score。
   - shadow log 每輪比對「shadow 預測產出 vs 實際 ExecutionResult」,誤差率進監控。

---

### P1-07 多套成本表並存,語意需單元測試鎖定

**現象/位置**:
- `core/config.py::COST_TABLE`(含 `unreachable_pit=None`=不可進入)
- `core/config.py::HIT_TABLE`(擊中次數)
- `planning/planner.py::enter_cost`(輸入成本,`unreachable_void` 特例、`unreachable_pit` 視同 `reachable_pit` cost 1)
- `v3/actions.py::dig_cost`(`unreachable_pit/one_hit_rock` 未列 → 回 0 → final_v1 中不可 dig)

**影響**: 同一格標籤在四張表語意不同(HIT vs 進入 vs dig)。`unreachable_pit` 在 `enter_cost` 有特殊處理，而 `dig_cost` 透過 `PIT_LABELS` 也允許挖掘；這是需要明確測試與文件化的語意差異，不應未經 replay 就直接宣稱是成本漂移或強制統一。

**建議改法**:
1. 先定義每個 API 的權威語意：`COST_TABLE/enter_cost` 是路徑進入成本，`dig_cost` 是實際 dig 消耗，`HIT_TABLE` 是擊中次數；只有在遊戲規則確認一致後才合併實作。
2. 新增 `tests/` 的參數化語意測試，對每個 `DEFAULT_CLASSES` 標籤鎖定目前預期值；若要改成完全一致，必須另附 replay 前後產出比較。
3. 將 `COST_TABLE`/`HIT_TABLE` 標記為被 `dig_cost`/`enter_cost` 取代的 legacy,或直接讓兩函式引用同一 dict。
4. 驗證方式: pytest 參數化 10 標籤 assert 一致性 + 語意註解。

---

### P1-08 炸彈/鑽頭 footprint 兩份定義重複

**現象/位置**: `v3/actions.py::get_drill_targets/get_bomb_targets` vs `core/mechanics.py::get_drill_affected_cells/get_bomb_affected_cells`(subagent 報告同定義;查證 L28-46)。final_v1/`_apply` 用 mechanics,executor/仿真用 actions?實際 split 易漂移。

**影響**: 若任一邊被改(例如爆炸波及範圍調整)另一端未同步,planner 規劃的收益與 executor 實際行為不一致 → 規劃偏差。

**建議改法**:
1. 單一來源: `core/mechanics.py` 作為唯一定義,`v3/actions.py` 的函式改為直接委派(或移除,保留相容 alias)。
2. 加一致性測試: 每個位置 `actions.get_X == mechanics.get_X`。
3. 驗證方式: pytest + 註解引用。

---

### P1-09 `_dismiss_mining_overlay_if_needed` 每輪 OCR,成本高

**現象/位置**: `mining_service.py:197-216`(每 loop iteration 對 y=210..550 ROI 全寬 OCR,`max_servers=1`)。只有當「挖到 pit」後的輪次才需要 dismiss,但現在每輪都做。

**影響**: 每輪多一次 OCR(本地或遠端 OCR server 呼叫),在慢 OCR server / NAS 環境拉高迴圈延遲,直接拉長時間佔用(一台 6 分鐘 session 可能 100+ 輪)。

**建議改法**:
1. 只在「上一輪執行過 dig 並判定挖到 pit」或「executor 回報挖到 pit」的下一輪做 overlay 檢查;否則跳過。
2. 或把檢查頻率設為 config(如每 N 輪,與 `_PICKAXE_OCR_VALIDATE_EVERY` 同風格)。
3. 驗證方式: telemetry 加 `overlay_ocr_calls` 計數;對照改前/改後每輪平均 latency(`tools/analyze_mining_logs.py` 或自訂)。

---

### P1-10 截圖重複(主 loop + overlay + item 預檢 + executor 驗證)

**現象/位置**:
- `mining_service.py:684`(主 loop screenshot)、`:688`(overlay 命中時重截)、`:536`(`_verify_items_pre_execution` 無 WS 時再截)、executor 每 dig 驗證重截、每 item 步驟重截。
- CNN 分類也以截圖輸入,單輪可能 3-4 張截圖。

**影響**: ADB 路徑截圖+分類是最貴步驟;重複截圖在慢裝置直接限縮每 session 的總步數與總產出。有 `shared_frame` 機制但未覆蓋 executor(executor 要求最新盤面,合理);overlay 與 item 預檢兩處可優化。

**建議改法**:
1. `_verify_items_pre_execution`: 無 WS 時,用本輪已拍的 `shared_frame` 做 OCR(簽名)而不是新截圖 — 檢查 `check_drill_num(d, frame=live_frame)` 已傳 frame,可傳 `shared_frame`。
2. overlay dismiss 後的重截(`:688`)只在下一次 `classify_board` 需要時做;但若 overlay 真的蓋住,原本 frame 作廢,重截必要。可接受,但要量化。
3. telemetry 加 `screenshots_per_round` / `classify_calls` 計數,進 `MiningTelemetry` log。
4. 驗證方式: 對比改前後一輪的平均 `screenshot+classify` 時間分佈;確認每輪截圖次數下降。

---

### P1-11 shadow 每輪跑一次完整搜尋,CPU 雙倍開銷

**現象/位置**: `_compute_shadow_plan` 每輪 `plan_final_v1`(預設 250ms budget,與主 planner 相同)。

**影響**: shadow 開啟時每輪 CPU 時間近似 2 倍;在樹莓派/舊 PC 直接拉長迴圈。全裝置已切 final_v1,若有人開 shadow 長期跑,成本明顯。

**建議改法**:
1. shadow 降採樣(每 N 輪一次,建議 N=3)或納入 CPU 負載門控。
2. shadow 的 time_budget 壓低(如 100ms)只取 top-k 對照用途。
3. 驗證方式: telemetry 加 `shadow_elapsed_ms` 統計 + 採樣率。

---

### P2-12 生命週期常數群硬編碼,應集中或設成 config

**現象/位置**:
- `USE_ITEMS`(L99),`_MAX_EMPTY_PLANS=3`(L102),`_MAX_IDENTICAL_BOARDS=3`(L105),`_PICKAXE_OCR_VALIDATE_EVERY=5`(L120),`_PICKAXE_DRIFT_TOLERANCE=2`(L124),`_PICKAXE_REWARD_DRIFT_MAX=10`(L130),`count<5` 門檻(L590),`zero_streak_limit=2`(L597)。

**影響**: 調整需改程式+重發;不同裝置(網路/CPU 特性)無法各自調適;也讓 replay 工具無法對齊同一參數。

**建議改法**:
1. 把「行為門檻」進 `DEFAULT_DEVICE_CONFIG`(per-device override): 建議 `mining_min_pickaxe=5`、`mining_empty_plan_limit=3`、`mining_identical_board_limit=3`。
2. `USE_ITEMS` 改 per-device flag(`enable_mining_items`),預設 true。
3. 純常數(`_PICKAXE_*`)集中到 `core/config.py`,不變行為;新增 config round-trip 測試保證新欄位不摔。
4. 驗證方式: config_manager round-trip pytest。

---

### P2-13 final_v1 自適應 beam/branch 與 deadline 邊際硬編碼

**現象/位置**: `final_v1/planner.py:227-233`(21 列 → beam_width=14, branch_width=8)、`:244`(`0.85` 安全邊際)。

**影響**:
- 大盤收窄寬度是實測經驗值,但硬編碼無法隨裝置 CPU 差異自我調整。
- 0.85 邊際是對「deadline 檢查粒度 + GC/OS 抖動」的對策,但不同環境抖動不同。

**建議改法**:
1. `PlannerConfig` 加 `large_board_beam_width`/`large_board_branch_width` 與 `deadline_margin`(0.0~0.95),由 config 或環境探測注入。
2. 保留預設值不改變行為,但開放覆寫。
3. 驗證方式: `sim_test_planner` 掃不同 margin/beam 對 p99/max 的影響,記錄到 docs。

---

### P2-14 known_pits 用 `unreachable_pit` 標籤表示「已知但未確認可達」

**現象/位置**: `final_v1/planner.py:234-241`:把 `known_pits` 覆寫板內 dirt/rock 為 `unreachable_pit`。

**影響**:
- `unreachable_pit` 在多處語意是「視覺判定的不可達」(然後由 `promote_after_dig` 剝前綴),在此卻被當作「已知礦位置」。若未來 `is_frontier_diggable`/`dig_cost` 對 unreachable 的判斷或 promote 邏輯調整,會波及此處。
- 目前行為正確(不可 dig、可被 promote、`is_pit` 算礦),但語意耦合是隱患。

**建議改法**:
1. final_v1 內部改用自訂中間標籤(如 `known_pit`)或獨立 flag,只在 `evaluate_state`/`pit_clusters` 讀取,不污染分類標籤空間。
2. 至少加註解與測試鎖定「known_pits 不影響可達性擴散」。
3. 驗證方式: 測試: 21 列板含 known_pits,斷言 promote/dig 行為與「直接標 unreachable_dirt」不同之處被文件化。

---

### P2-15 DepthTracker: ADB 無絕對深度,捲動偵測保守

**現象/位置**: `depth_tracker.py::RATIO_FLOOR=0.55`(L109);`set_absolute_depth`(L123-133)只有 WS 用。ADB 只有相對捲動,重疊結構不足時回 `0` + `last_uncertain`。

**影響**: depth 對 ADB 僅為粗估值:滾動很大(>6)或全空地盤面時 uncertain 增加;`depth` 影響 log/telemetry 但不影響 planner(planner 只看板)。屬觀測品質問題。

**建議改法**:
1. uncertain 累計統計進 telemetry(`depth_uncertain_ratio`)。
2. 若 map_recorder 有累積「已見地形 tape」,捲動後用 tape 對齊給出絕對深度(與 WS 同思路)。
3. 驗證方式: replay `tools/track_pits_replay.py` 已共享 `best_scroll`;加測試比對 `best_scroll` 與人工標記捲動之誤差。

---

### P2-16 Telemetry 五軌分散,未統一 schema

**現象/位置**:
1. `_log_planner_stats`(mining_service.py:439)
2. `MiningTelemetry` log 行(mining_service.py:858, 891-900) — 已是機讀格式但嵌在 log
3. `MiningMapRecorder`(`utils/mining_map_recorder.py`)session JSONL
4. `RLRecorder`(miner/rl/rl_recorder.py)
5. `ws_validator` protocol-validate(0x0c01/0x0402 交叉驗證)

**影響**: 分析挖礦效率時要在不同 format 之間 join;`tools/analyze_mining_logs.py` 只能吃 log;JSONL 與 log 無法直接 merge;欄位名不統一(`planner_name` vs `planner`);後續 spec 提出的任何 KPI 都要兩處改。

**建議改法**:
1. 定義單一 `MiningRoundEvent` dataclass(planner / source / exec_profile / depth / board_rows / known_rows / first_step / score_breakdown / steps_received / steps_completed / shovels / drills / bombs / inv_before / inv_after / reasons / elapsed / screenshots / overlay_ocr_calls),每輪一行 JSONL 輸出到 `logs/mining_telemetry_<ip>.jsonl`。
2. `MiningMapRecorder.round` 改吃該 event(或併入),RLRecorder 交付保留(它是 RL 訓練用途)。
3. `tools/analyze_mining_logs.py` 加 JSONL reader;dashboard 如需加 mining 統計再延伸。
4. 驗證方式: 舊 replay 資料與新 JSONL 對同一 session 輸出一致 KPIs(即平行跑,直到統計工具切換完成)。

---

### P2-17 worktree 目錄放在主 repo 內,污染搜尋與工具

**現象/位置**: `worktree/h5-ocr-complete-repair/`、`worktree/h5-live-validation-fix/`、`worktree/separate-tasks-config/` 等在主 repo 資料夾內;`git status` 顯示 `?? worktree/`(未追蹤);grep/glob/agent 常掃到多份 `mining_service.py`/`bot_config.json`。

**影響**:
- 任何全文搜尋、`git grep`、agent 讀檔都命中重複,易誤改/誤讀;A/B 比對原始碼混進舊。AGENTS.md 的 worktree 慣例(git worktree 本質是 repo 外資料夾)被打破,收斂困難。

**建議改法**:
1. 既有 worktree 的工作結束後併回 main 並整棵刪除。
2. 未來 worktree 一律建在主 repo **外**(如 `..\菇勇者全自動掛機-wt\<name>`),不進 repo 資料夾。
3. 或在 repo `.gitignore` 排除 `worktree/`,並在 AGENTS.md 補充路徑慣例。
4. 驗證方式: `git grep mining_service` 只命中 1 份;`git status` 不再見 `worktree/`。

---

### P2-18 final_v1 object cluster bonus 平方成長,大簇偏好需驗證

**現象/位置**: `scoring.py:170` `completed_bonus += cluster_size * (cluster_size-1) * CLUSTER_COMPLETION_MULTIPLIER(2)` — 隨 cluster 大小平方成長。

**影響**: 大簇完成獎勵遠高於小簇(3 格簇 bonus 12,10 格簇 bonus 180 vs 收礦值 100),可能讓 planner 系統性偏好大簇、忽略可行的小數量產出；目前尚無 replay 證據，仍可能是刻意設計(real reward 是整簇)。

**建議改法**:
1. 先用 `tools/mining_sim_eval.py` + real replay 掃「小簇 vs 大簇」分布偏好,量化「被跳過的小簇數量」；在取得證據前不調整 scoring。
2. 若證實過度偏向,改為 `max(0, size-1) * CLUSTER_COMPLETION_MULTIPLIER`(線性)或對 `size` 設上限。
3. 驗證方式: replay 上比較改前/改後 `pits` 與 `clusters_completed` 分布;KPI 同 spec 2026-07-11「勝出標準」。

---

### P2-19 遊戲規則 (mechanics) 全域常數散落各處

**現象/位置**:
- `core/config.py`: COST/REWARD/HIT 表、GRID_CFG
- `final_v1/scoring.py`: PIT_VALUE=10、SHOVEL_COST=1、ITEM_COST=3、權重群(PATH_BONUS 0.25、PIT_PULL 0.8…)約 10 個魔法數字
- `smart_planner.py`: cost_item=2.99、weight_chest=10、weight_depth=5、weight_h=1.5
- `item_planner.py`: TOOL_MIN_COST_SAVINGS=2.0、CONSERVATIVE_FACTOR=0.5 …

**影響**: 各 planner 的「1 顆礦值多少/道具值多少」不一致(2.99 vs 3.0 vs 3.6),評分模型彼此不可比;做 A/B 或調參時要在散落處找。

**建議改法**:
1. `final_v1/scoring.py` 的權重群集中成一個 dataclass `ScoringWeights`,支援純函式 scan(monkeypatch 已是做法,可正規化)。
2. 對外 KPI 兌換率統一 3.0(已符合);shadow/比較用同一組權重。
3. 不要求 v1/v2/v3/v4 統一(它們是 legacy 基準),但在文檔註記各版參數用途。
4. 驗證方式: 以 `planner-eval` skill 的 toolchain 掃權重敏感度,留一份 tuning 記錄。

---

### P2-20 測試與評估工具散落,無集中 harness

**現象/位置**:
- `miner/scripts/` 下 20+ 個測試腳本(test_*.py、sim_test_planner.py、optimize_algo.py…)
- `tools/mining_sim_eval.py`、`tools/replay_real_boards.py`、`tools/analyze_mining_logs.py`(planner-eval skill 的進入點)
- `tests/fixtures/miner_production_boards/` 已有真實 board fixtures
- AGENTS.md 明言不要整包 pytest(重依賴)

**影響**: 
- 多數 scripts 是一次性除錯工具,重複性低、職責不清;board fixtures 齊全但似乎主要給 replay 用,planner 行為測試覆蓋可能不足。
- 新增 regression 測試容易被忽略(沒集中地方)。

**建議改法**:
1. 確立 `tests/test_miner_*.py` 為唯一 pytest 入口(import 乾淨、不載真裝置),把關鍵行為搬進其中:
   - planner 決定性:同盤面同 seed 同參數 → 同 plan(reproducible)
   - planner 合法性:任何 steps 的座標/型別必須被 `_cnn_valid_targets` 含納(第一步)且後續步合法(可用 fixtures)
   - 成本表一致(P1-07)、footprint 一致(P1-08)
   - 空盤面/全爛盤 → 單步 dig fallback(不空 plan)
   - 捲動切斷:floor7 開 → plan 停止,不回報下一 viewport
   - 道具:0 庫存不出道具、分級成本行為、parity tie-break 可重現
2. 指定式的 scripts 移入 `miner/scripts/` 保留,但註記「已由 pytest 取代」或標「除錯工具」。
3. 驗證方式: `python -m pytest tests/test_miner_*.py -q` 全綠;`replay_real_boards.py` 產出 baseline 存檔(如 `tasks/eval_baseline.txt`,目前已有)。

---

### P2-21 item_planner 專屬盤面啟發法與 final_v1 重疊

**現象/位置**: v1 分支(`mining_service.py:427`)先跑 `find_tool_candidate`(item_planner 全掃),命中才用,否則 `plan_smart`;而 `plan_smart` 的 `get_valid_actions` 也有道具(BFS exposure + drill/bomb)。final_v1 又把道具決策內建。三處道具決策並存。

**影響**:
- v1 有一個「先單步道具、再 A*」的兩段式;道具決策邏輯 punch through 到三個規劃器,每次調參遺漏任一處就偏。
- 非 bug,但維護成本高,且`item_planner` 的 heuristics(多礦群、bottom triplets、edge strip)並未進 final_v1 — 這些「畫面外收益估計」在 WS 21 列存在時多餘,在 CNN/ADB 卻有價值(=位能場沒有畫面外資訊)。

**建議改法**:
1. 不重寫(保守): 文件化三處各自職責(v1 path item 先行 / final_v1 內建)。
2. 把 `item_planner` 的「CNN 7 列的隱藏/畫面外礦群估估」整理成一個 `estimate_hidden_gain(board, r, c, tool)` 純函式,供 final_v1 在 `visible_rows=7`、`known_board=None` 時以低權重加分(解決 P1-05 的部分),其餘環境不加。
3. 驗證方式: replay 對照加/不加的 planner score 與實際產出變化量。

---

### P2-22 `_step_signature` 與 `_blocked_action_keys` 兩套 signature,易脫鉤

**現象/位置**: `_step_signature`(5-tuple type,item,pos,target,action;mining_service.py:293)、`_blocked_action_keys`(改 4-tuple kind,item,r,c;L332)、`_cnn_valid_targets`(action-key set;L318)、executor 黑名單位型別不同(NoBoardChange 存 5-tuple,blocked_actions 餵 v3/v4 時切 `{sig[:3]}`)。

**影響**: 同一「非法操作」在不同環節以 3/4/5-tuple 呈現,V3/V4/`_blocked_action_keys`/`_cnn_valid_targets` 的座標格式必須各自轉換;邏輯分散、易犯錯(例如 item 步驟的 pos 是落點,dig 的 pos 是目標,語意 Balance)。

**建議改法**:
1. 統一定義 `ActionKey = (kind: str, item: str, row: int, col: int)`(final_v1/types.py:ActionKey 已是此格式),`blocked/valid` 統一存 ActionKey;executor 黑名單也改用行動鍵。
2. v3/v4 的舊 3-tuple 介面只保留相容 wrapper。
3. 驗證方式: 單元測試——一個 NoBoardChangeError 在 blocked/valid/executor 三處比對等價。

---

## 3. 建議執行順序 (Roadmap)

> 依類別與依賴排序。每個 commit 對應一個可驗證項目,並遵循 AGENTS.md:GSD worktree 隔離(worktree 建在 repo 外 → 見 P2-17)。

### Sprint 1 — 正確性與防護 (P0)
1. P0-02 fallback 標記修正(KPI 可信度，範圍小且已確認)
2. P0-04 主迴圈全域例外保險(以 try/finally 確保 recorder 收尾，防止裝置執行緒被炸掉)
3. P0-03 道具黑名單誤殺修復(先補 WS/ADB fake 測試，再決定 retry/門檻)
4. P0-01 強制下挖改進(先量測實際下樓成功率，再調整選格策略)

### Sprint 2 — 效率與兩後端一致 (P1)
5. P1-09 overlay OCR 頻率
6. P1-10 截圖縮減 + screenshot 計數 telemetry
7. P1-05 底列炸彈低估:先文件化+telemetry,再做 map/WS 擴充
8. P1-06 對照組裝置 + shadow 升級(replay 產出同構比對)
9. P1-07 成本表語意鎖定 + 測試(先不合併實作)
10. P1-08 footprint 單一來源

### Sprint 3 — 可配置性與可維護 (P1-P2)
11. P2-12 常數進 config
12. P2-13 PlannerConfig 參數化
13. P2-16 統一 telemetry schema
14. P1-11 shadow 降採樣

### Sprint 4 — 測試與清理 (P2)
15. P2-20 pytest harness 建立(含本 spec 全部測試項目)
16. P2-22 signature 統整
17. P2-14 known_pits 語義隔離
18. P2-15 depth telemetry
19. P2-17 worktree 清理
20. P2-18 cluster bonus 驗證
21. P2-19 權重集中
22. P2-21 hidden-gain 純函式

## 4. 驗證總則

每項改動後,依 AGENTS.md 驗證流:
1. 目標 pytest: `python -m pytest <相關測試檔> -q`(不要整包)。
2. `python -m py_compile` 受影響檔案(避免掃重依賴)。
3. 挖礦相關改動額外跑: `python tools/replay_real_boards.py`(在有 mining map fixtures 的裝置)與 `python tools/mining_sim_eval.py`,對照 baseline(`tasks/eval_baseline.txt`)。
4. 真人盤面驗測(有機會時): 用手指標盤截圖 → `python miner/scripts/debug_with_image.py <screenshot.jpg>` 檢查 plan 合理性。
5. H5/WS 與 ADB 都要驗(BVT 原則,見 dual-backend-task-dev skill)。
6. 記錄每項的 before/after 到 `tasks/lessons.md`。

## 5. 非目標 (此 spec 不處理)

- 不移除/改名任何既有 planner 版本(基準需保留)。
- 不在此版本引進 RL、POMCP、外部求解服務或新遊戲協議。
- 不自動更動任何裝置的 planner 設定(設定變更由使用者決定)。
- 不把 `mining_service` 拆成新架構(水管重構風險大,留待獨立 spec)。

## 6. 審查修訂 (2026-08-05)

本版根據對目前程式碼與測試的交叉檢查，補充以下判定：

- **已確認問題**：P0-02、P0-04、P1-09、P1-10；P1-05 與 P1-08 的後端/footprint 差異也有直接程式證據。
- **部分成立、需先量測**：P0-01 的強制下挖品質、P0-03 的道具黑名單誤殺、P1-06 的 shadow 對照完整性。
- **需避免過度修正**：P1-07 的成本表不應直接合併，必須先鎖定遊戲語意；P2-18 在 replay 數據出現前維持觀察項。
- `P2-17 worktree` 屬 repository hygiene，不是挖礦行為修復；處理時必須避開既有未提交變更與未追蹤文件。

後續每一項開工前，先把「現象」改寫成可重現的測試或 telemetry 判據；沒有證據的項目只做觀測，不直接改 planner scoring 或遊戲規則。

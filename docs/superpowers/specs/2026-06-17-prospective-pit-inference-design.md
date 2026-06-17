# Prospective Pit Inference — v5 改進 / v6 設計

> **狀態:已撤回 (2026-06-17)。** 實作完成且 80 測試通過,但事後用真實 log 序列
> 驗證發現前提錯誤,功能已 revert。結論見文末「驗證後撤回」。保留本文件作為記錄。

## 問題

v5 planner 評估炸彈/鑽頭落點時，只對「已確認的坑」（`reachable_pit` / `unreachable_pit`）計算命中數。

當視野內只出現 3×3 cluster 的頂排（3×1），planner 看不到下方兩排，因此低估了「把炸彈中心移到 3×3 中心」的收益，可能選出次優落點。

## 領域規則（使用者確認）

> 水平相鄰兩個以上的坑（同一排、連續）= 必然是 N×N 正方 cluster 的一部分，其中 N = 連續寬度。缺少的列永遠在下方（尚未挖開或尚未 scroll 進視野）。

機率視為 1.0，不需驗證。

## 核心設計

**單一改動：擴大 `pit_cells_hit` 的定義。**

在計算炸彈/鑽頭落點收益時，命中格子計為「坑」的條件從：

```
is_pit(cell)
```

改為：

```
is_pit(cell) OR (r, c) in prospective_pits
```

`prospective_pits` 是推斷必然是坑但尚未挖開的格子集合，由新函數 `find_prospective_pits(board)` 產生。

### find_prospective_pits(board)

1. 掃描每一排，找出所有水平連續坑串，寬度 N ≥ 2
2. 對每條寬度 N 的串（row r, cols c..c+N-1）：
   - 往下檢查 row r+1 到 row r+N-1：
     - 若 row index ≥ len(board) → 超出視野，**停止並跳過此格**（bomb/drill 打不到）
     - 若任一格已挖開且不是坑（empty/dug_pit）→ **此串失效，跳過整條**
     - 若任一格已挖開且是坑 → 繼續往下確認（那排已知，不算 prospective）
     - 若是未挖開格（dirt / rock / one_hit_rock）→ 加入 `prospective_pits`
3. 回傳 `set[tuple[int, int]]`

**注意**：`prospective_pits` 只用於評估炸彈/鑽頭的命中收益，不用於挖掘動作（個別 dig 仍受路徑可達限制，不受影響）。

### 移除 _incomplete_bottom_squares()

cost 模型正確後，planner 自然會選出命中更多坑的落點，不需要懲罰機制強制等待。移除此函數及其常數 `INCOMPLETE_SQUARE_PENALTY`。

## 效果

| 情境 | 改前 | 改後 |
|------|------|------|
| 3×1 可見，炸彈評估 | 最多 3 坑命中 | 正確評為 9 坑命中（落點移到 3×3 中心）|
| 2×1 可見，炸彈評估 | 最多 2 坑命中 | 正確評為 4 坑命中 |
| 完整 3×3 已可見 | find_clusters 正常 | 不干涉（串失效因下方已是已知坑）|
| 下方挖出非坑 | 不處理 | 串失效，正確回退為多個 1×1 |

cost 模型修正後，DFS 的最小 cost 路徑自然涵蓋整個預期 cluster。

## 實作範圍

| 檔案 | 變動 |
|------|------|
| `miner/v3/clusters.py` | 新增 `find_prospective_pits(board)` |
| `miner/v5/planner.py` | `_action_priority()` 擴大 pit_cells_hit；刪除 `_incomplete_bottom_squares()` 及 `INCOMPLETE_SQUARE_PENALTY` |

可以直接在 v5 上改，或複製為 `miner/v6/` 保留 v5 做對照。

## 不變的部分

- DFS 結構、rolling-horizon、branch-and-bound 上界剪枝
- v5 所有歷史先驗（`priors.json`）、期望 cost 下行、pit-column bias
- find_clusters() 邏輯
- 所有其他 action priority 權重

## 審查後判定（最終 code review 提出、經領域分析後確認非缺陷）

審查員提出兩個 Important，逐一驗證後判定均為設計上正確的保守行為，不需修改：

1. **`pits_collected` 遙測含推斷坑**：`pits_collected` / `pits_at_start` /
   `exit_guard_required` 經全 codebase 搜尋確認無任何 live 決策邏輯消費（只進
   `mining_service.py` log 訊息與序列化），且數值在 planner 模型內自洽（規劃推斷坑，
   亦預期收集）。唯一受影響者為離線 planner 比較工具 `tools/compare_v3_v4_planners.py`，
   屬離線分析語義差異，非 live 正確性問題。

2. **與 `_incomplete_bottom_squares` 的 -400 懲罰交互**：推斷格只在「cluster 延伸到/超出
   視野底緣且確認高度 < 寬度」時才會落到底行被懲罰，而該情境下現在炸彈只能打到視野內的部分
   列（cluster 有一列在螢幕外），延後到 scroll 後再炸才是 bomb-efficient 的正解。視野內完整
   cluster（主案例）height == width 不觸發懲罰，功能正常（已驗證 3×1 → 炸彈落 (2,2) 中心 →
   收 9 格）。

無 Critical 問題。核心推斷邏輯、O(rows×cols) 效能、每回合重截圖的自我修正特性均經確認正確。

## 驗證後撤回 (2026-06-17)

正確性測試全過後,進一步做競技分數與真實準確率驗證,發現**前提錯誤**,功能 revert。

### 量測結果
- **sim 比較(40 局,同 seed)**:v1=825 > v3=820 > v4=799 > v5=774。v5 最低;開/關
  prospective 的 v5 分數完全相同(774→774),sim 上是 no-op。
- **sim 觸發準確率**:prospective 在 sim 觸發 0.62%,且 118/118 全錯(打到方塊隔離環的
  dirt)。
- **真實 log 序列追蹤**(`track_pits_replay` 對齊全域 tape,1183 幀):806 個推斷格,
  **0 個**後來顯形為坑;56% 整局維持 dirt、40% 維持 rock、2% 被挖成 air、0% 變坑。

### 根本原因(使用者澄清)
新礦是**從視野下方捲動進來**的 —— cluster 缺的格在**螢幕外**,不是螢幕上的 dirt。
而本實作 `find_prospective_pits` 只推斷**視野內**(`rb < rows`)pit run 正下方的 dirt,
並明確跳過視野外。因此它標記的是螢幕上的真實 dirt/rock(資料證實 96% 永遠是 dirt/rock),
**標錯了格子**。在真機上會把非礦格當坑,讓 planner 把炸彈/鏟子浪費在沒礦的位置。

### 正解方向(未實作)
使用者真正的需求(預期底緣 run 會長成完整 cluster、規劃最佳收集路徑)對應的是
**底緣 run + 螢幕外延續的時序規劃**,現有 `_incomplete_bottom_squares()` 已是簡版正解
(偵測底緣 height < width 的 run 並延後炸它,等捲動揭露)。若要強化應從此處著手,
而非標記視野內 dirt。唯一能對「螢幕外格是否為坑」定論的 ground truth 是 WS 挖掘獎勵訊號。

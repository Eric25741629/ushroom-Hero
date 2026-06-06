# 挖礦演算法分析與礦物出現率校正 (2026-06-05)

> 本文件為一次性深度分析的完整紀錄。觸發需求：分析 v1-v4 挖礦演算法、研究真實
> 挖礦 log、修正礦物出現率、僅保留最佳 3 個演算法、研究是否有更有效演算法
> (單步 < 0.3s)、跑模擬驗證後收尾。
>
> 對應工具：`tools/analyze_mining_logs.py`、`tools/track_pits_replay.py`(礦脈時間追蹤)、
> `tools/mining_sim_eval.py`(已校正)、`tools/compare_planners.py`、`tools/replay_real_boards.py`。
> 對應 skill：`.claude/skills/planner-eval/`。

---

## 0. 結論 (TL;DR)

1. **礦物出現率與形狀必須「沿時間追蹤 pit」才量得對**(user 指正)。單張 board 連通分量會
   **嚴重低估大 cluster**：3x3 礦團跨 3 個 tape row、隨畫面下捲被**逐步收集**，所以在任何
   單一 frame 都不會出現完整 9 格 → 用單張算會誤判「沒有 3x3」。
   **時間追蹤回放 (`tools/track_pits_replay.py`) 證實：3x3 真實存在且常見。**
2. **真實礦物分布 (時間追蹤 59 sessions)**：cluster 是**正方形 1x1/2x2/3x3**(原 sim 形狀設計正確)；
   數量占比 **1x1 66% / 2x2 18% / 3x3 17%**；但 3x3 雖只占 17% cluster，卻是 **~52% 的礦格**
   → 這是個**富 cluster** 的 regime，cluster-aware 規劃有價值。
3. **真實 spawn 密度 = ~3.6% 的 tape 格** (不是單張快照的 0.99%；快照只是 standing 殘量，pit 被快速
   收集所以看起來少)。舊 sim 的 ~33% spawn 是 **~9x 過高**，但**形狀是對的**。
   已重新校正 sim：正方形 1x1/2x2/3x3 + 密度 ~4% spawn。驗證：sim 玩到的 standing 密度 0.89-1.04%
   ≈ 真實 0.99%。
4. **移除 v2** (保留最佳 3 = v1 / v3 / v4)。理由與 cluster 無關：1067 個真實 board 上 v2 有
   **18.8% 超過 0.3s、最慢 1841ms**，是唯一不及格的演算法。
5. **DEFAULT 維持 v4**。真實 board 上 v4 最快 (mean 1.1ms / max 46ms)、0 違規、且帶 buried-pit 的
   unseal-corridor fallback。**v1 (A*) 在校正後 sim 仍小幅最佳** (score 948、最省鏟 cost 186) —
   因為 A* 會精確找出「用炸彈一發清 3x3」的下法。
6. **優化已執行**：給 v3 加 wall-clock deadline (唯一會偶爾破 0.3s 的保留 planner，453ms → 262ms)。
7. **更正先前誤判**：早先「沒有 3x3、v4 過度設計」的結論**錯了**(用了單張快照的瑕疵量法)。
   3x3 真實存在，v4 的 cluster 機制有其價值。

---

## 1. 方法論

四層證據，互相交叉驗證：

| 層 | 工具 | 看什麼 |
|---|---|---|
| A. 真實 log cell 統計 | `analyze_mining_logs.py` | 單張 board 的 cell 分布/timing/道具庫存 |
| **A'. 礦脈時間追蹤** | **`track_pits_replay.py`** | **對齊連續 frame 的捲動 → 重建 global tape → 真實 cluster 大小/密度** |
| B. 校正後模擬 | `mining_sim_eval.py` + `compare_planners.py` | 同 seed 跨 planner full-session 品質 |
| C. 真實 board 重放 | `replay_real_boards.py` | 每 planner 餵真實 board，量 empty% + **單步時間** (硬限制驗證) |

> **關鍵教訓 (user 指正)**：層 A 的單張快照連通分量會把 3x3 拆散誤判成小礦脈。必須用層 A'
> 沿時間追蹤每個 pit (含已被挖掉的 `dug_pit`)，才量得到真實 cluster。**改 planner 前的 regime
> 量測，方法錯比沒量更危險。**

---

## 2. 真實礦物分布 (層 A' 時間追蹤)

`track_pits_replay.py` 把每個 session 的連續 frame 依捲動量對齊成一條 global tape，標記每個
**曾經是 pit** 的 tape 座標 (即使後來被挖成 `dug_pit`)，再對重建的 pit map 做連通分量。

### 2.1 真實 cluster 大小分布 (59 sessions / 137 clusters)

| cluster | 數量占比 | 形狀 |
|---|---|---|
| 1x1 (size 1) | 66% | 單格 |
| 2x2 (size 4) | 18% | 正方 |
| **3x3 (size 9/8\*)** | **17%** | 正方 (\*size 8 = 收集中被截到的 3x3) |

- **最大 cluster = 9 格 (3x3)**，無更大。
- 3x3 占 17% cluster 但 **~52% 的礦格** (22 個 3x3 × 9 = 198 格 / 全部 ~378 格)。
- **對照單張快照誤判**：層 A 單張連通分量說「63.5% 單格、0 個 3x3」— 錯。3x3 被逐步收集，從不在
  單一 frame 完整出現。

### 2.2 真實密度

- **SPAWN 密度 = 3.64%** 的 tape 格 (重建 378 pit 格 / 10392 tape 格)。
- **STANDING 密度 = 0.99%** (單張快照；pit 被快速收集所以殘量少)。
- 兩者差異是「被收集」造成的，spawn 才是該餵給 sim 的正確值。

### 2.3 策略與時間 (現行 v4，層 A)

- 每回合策略分布：**no_pit 75% / has_pit 25%** — 注意這是**每回合**視野 (多數回合可見盤面剛好沒
  可達 pit，因為 pit 稀疏且收集快)；但**累積分數**主要來自那 25% has_pit 回合的 cluster。
- plan 時間：mean 1.23ms、p99 15ms、max 27ms；0% 超過 300ms。

### 2.4 道具庫存

- 炸彈：mean **588**、max 883。鑽頭：mean 59、max 343。炸彈在真實是豐沛資源；鑽頭輕度受限。
- 富 cluster + 炸彈豐沛 ⇒ 「炸彈一發清 2x2/3x3」是高價值下法，planner 應善用。

---

## 3. 礦物出現率校正

### 3.1 舊 sim 哪裡錯、哪裡對

- **形狀對**：舊 `_place_clusters_in_range` 鋪正方形 1x1/2x2/3x3 (`n1/n2/n3`) — 與真實一致。
- **密度錯**：每 60-row 鋪 ~118 pit 格 = **32.8% spawn**，真實只有 ~3.6% → **~9x 過高**。
- 過密讓 board 充滿 cluster，膨脹了所有 score、誇大了道具利用率。

### 3.2 校正後 (已套用 `tools/mining_sim_eval.py`)

- 正方形 cluster，side 依真實 PMF `CLUSTER_SIDE_PMF = {1:0.66, 2:0.17, 3:0.17}` 抽樣。
- 目標 `PIT_DENSITY = 0.036` (spawn ~4%)。1-ring 隔離維持 cluster 邊界清楚。
- 實測：fresh spawn 4.06%、mix 1x1 67%/2x2 20%/3x3 13%、**含 3x3**。
- **驗證**：sim 玩到的 standing 密度 v1 1.04% / v3 0.89% / v4 0.92% ≈ 真實 0.99% → 校正自洽。

> 註：先前一版誤把礦物改成「無 3x3 的小礦脈 + 1.3% 密度」(用了單張快照的瑕疵量法)。已依
> 時間追蹤更正回正方形 + 3.6% 密度。

### 3.3 fallback 模型 (sim 公平性修正，保留)

planner 回傳空步時 sim 以「挖最便宜可達 frontier」推進並計 `fallback%`，避免單一 empty plan 被
當成 game over (真實會重拍重規劃)。

---

## 4. 四個演算法характеristика

| | v1 SmartPlanner | v2 (已移除) | v3 | v4 (default) |
|---|---|---|---|---|
| 核心 | 加權 A* (ε=1.5) | A* + has/no_pit 分流 | bounded best-first (heap) | bounded DFS depth=3 + B&B |
| 終止 | node 2000 | node budget | node 6000 +**新增 230ms deadline** | node 8000 + **250ms deadline** + depth=3 |
| 單步 <0.3s 保證 | 無硬時限但快 (max 60ms) | **無時限 → 失控** | **原本無時限 (max 453ms)** → 已修 | 有硬時限 |
| cluster 處理 | A* 精確找最省解 (含炸彈一發清 3x3) | — | 顯式 cluster 全覆蓋 bonus | cluster 完成 bonus + B&B |
| code | 253 行 | 683 行 (最複雜) | 409 行 | 822 行 |

### v1 — A* SmartPlanner
- `h = 10·剩餘礦 + 5·(未開底層)`，`f = g + 1.5h`；目標 = 礦清空 ∧ 底層打通。
- 枚舉所有暴露可挖格 + 可達空氣上的道具 (含炸彈/鑽頭)。因為 A* 精確最小化成本，**自然會用炸彈
  (cost 2.99) 一發清 3x3 (省 9 鏟)** — 不需要特製 cluster bonus。回傳整條最省路徑 → 校正後 sim 最省鏟。

### v2 — 已移除
- real board 重放：mean 182.6ms、p99 787ms、**max 1841ms、18.8% 破 0.3s**。歷史會 stuck。最複雜。**移除。**

### v3 — cluster-aware best-first
- 顯式 cluster 全覆蓋 bonus；嚴守「有礦不打通底層」。原本無時限 (max 453ms)，**已加 230ms deadline** → max 262ms。

### v4 — bounded DFS + branch-and-bound (default)
- depth=3 + B&B + cluster 完成 bonus + reverse-Dijkstra unseal corridor (解 buried pit) + 硬 250ms deadline。
- real board 最快 (mean 1.1ms / max 46ms)、27 回歸測試。富 cluster regime 下其 cluster 機制**有價值**
  (3x3 是 52% 礦格)，雖然分數未超過 v1 的精確 A*。

---

## 5. Benchmark

### 5.1 真實 board 重放 (1067 board, 層 C — 計時 ground truth，<300ms 硬限制)

| planner | empty% | mean ms | p99 ms | max ms | >300ms |
|---|---|---|---|---|---|
| **v4** | 0.28 | **1.1** | 12 | 46 | **0** |
| **v1** | 0.28 | 2.5 | 22 | 60 | **0** |
| **v3** (修後) | 0.28 | 3.7 | 36 | 262 | **0** |
| ~~v2~~ | 0.28 | 182.6 | 787 | **1841** | **201 (18.8%)** |

### 5.2 校正後 sim full-session (含 3x3, 30 seed, realistic 庫存)

| planner | score | pits | depth | cost | pit/sh | plan ms | fb% |
|---|---|---|---|---|---|---|---|
| **v1** | **948** | **47.8** | **164.9** | **186** | 0.3 | 3.08 | 0.0 |
| v3 | 926 | 46.9 | 161.7 | 201 | 0.2 | 5.51 | 0.0 |
| v4 | 925 | 46.8 | 160.8 | 200 | 0.2 | **1.07** | 2.7 |

- 即使含 3x3，三者品質仍接近 (~2.5%)，**v1 仍小幅最佳 + 最省鏟**(A* 精確找炸彈清 cluster)。
- v4 cluster bonus 是 A* 行為的啟發式近似，沒超過 A*，但 real board 最快 + 有 buried-pit 護欄。

---

## 6. 研究：有沒有更有效的演算法？

**現有三個已足夠，不需要全新演算法**，但理由與先前(錯誤)版本不同：

- 真實是**富 cluster** regime (3x3 占 52% 礦格)，所以 cluster-aware 規劃**確實有價值** — 先前
  「無 3x3 → v4 過度設計」的判斷是錯的。
- 但 **v1 的精確加權 A* 已經把 cluster 處理得最好** (自動找「炸彈一發清 3x3」)，校正後 sim 仍小幅
  領先 v3/v4。v4 的 cluster bonus + B&B 是同一目標的 bounded 啟發式，速度最快、real board 最安全。
- 每回合 75% no_pit 時，三者都退化成 greedy。25% has_pit 時，三者都會用道具清 cluster。
- 三個保留 planner 都滿足 < 0.3s (修 v3 後)，各有定位 → 不需要再造輪子。

### 未來 (中 ROI)
- **在校正後 sim 上重掃 v4 的 `DRILL_COST/BOMB_COST/MAX_DEPTH`**：現有常數是在 9x 過密的舊 sim 上
  掃的。真實炸彈豐沛 (mean 588) + 3x3 值 52% 礦格 → 降 `BOMB_COST` 鼓勵「炸彈清 3x3」可能讓 v4
  追上 v1。常數被 `test_miner_v4_planner.py` 鎖死，改動需同步重驗。
- **是否改 default 為 v1**：v1 sim 一貫小幅最佳 (score+省鏟) 且 0 punt，但 v4 real board 最快 +
  有 buried-pit corridor 護欄。建議先 live A/B 再決定，勿純憑 sim 翻 default。

---

## 7. 已執行的變更 (user pre-approved「立刻執行」)

| 變更 | 檔案 | 驗證 |
|---|---|---|
| 新工具：礦脈時間追蹤 (修正 cluster 量法) | `tools/track_pits_replay.py` | 證實 3x3 存在、spawn 3.64% |
| 校正礦物出現率 (正方 1x1/2x2/3x3 + 密度 ~4% spawn) | `tools/mining_sim_eval.py` | fresh 4.06% / standing 0.9-1.0% ≈ 真實 0.99% |
| sim 加 fallback 模型 + standing density 量測 | `tools/mining_sim_eval.py` | benchmark 可比 full-session |
| 移除 v2 演算法 | del `miner/v2/planner.py`/`debug_with_image_plan.py`/`test_miner_v2_planner.py` + 改 dispatch/config/PLANNERS | py_compile OK；無殘留 import；保留共用 classifier/service/types |
| v3 加 wall-clock deadline (230ms) | `miner/v3/planner.py` | real board max 453→262ms, 0 violation |
| 新工具：真實 log 統計 / 真實 board 重放 / planner 對比 | `tools/analyze_mining_logs.py`, `replay_real_boards.py`, `compare_planners.py` | 全可重現 |

測試：v3/v4 planner + device_config 67 綠、shared-infra 18 綠、integration 4、shovel 5、classifier 4。

> `miner/v2/` 套件保留，因 `classifier.py / service.py / types.py / visualization.py` 是 v3/v4
> 共用的 CNN 分類層；只移除 `plan_v2` 演算法。

---

## 8. 重現指令

```bash
# 礦脈時間追蹤 (真實 cluster 大小/密度 — 量 regime 的正確方法)
python tools/track_pits_replay.py

# 真實 log cell 統計 (單張，會低估大 cluster — 僅看 cell 分布/timing/道具)
python tools/analyze_mining_logs.py

# 真實 board 重放 (計時硬限制驗證)
python tools/replay_real_boards.py --planners v1,v3,v4

# 校正後 sim full-session 對比 (含 3x3)
python tools/compare_planners.py --planners v1,v3,v4 --runs 30 --seed 200 --max-iter 150
```

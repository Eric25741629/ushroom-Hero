# 挖礦演算法分析 + 礦物出現率校正 — TODO

Branch: feat/dragon-realm (analysis lives in tools/ + docs/, planner code untouched unless a fix is proven)

## 目標 (user request 2026-06-05)
1. 分析 v1/v2/v3/v4 挖礦演算法
2. 研究當前挖礦 log (已完成: tools/analyze_mining_logs.py)
3. 修正文檔 (game_rules.md / MINING_SCHEMA / planner-eval skill / CLAUDE.md)
4. 修正礦物出現率 (sim 比真實高 33x — tools/mining_sim_eval.py `_extend_tape`)
5. 僅保留最佳 3 個演算法 (移除最差的一個)
6. 研究是否有更有效的演算法
7. 限制: 單步動作 plan 時間 < 0.3s (real v4 已 mean 1.2ms / max 27ms)
8. 跑模擬測試才可收尾
9. 分析寫入 .md
10. 有可優化的立刻執行 (user pre-approved)

## 進度
- [x] 讀 sim_eval / board / schema / game_rules / mining_service legend
- [x] 寫 + 跑 tools/analyze_mining_logs.py → 真實 cell 分布 + cluster shape + timing
- [x] 量化 sim over-density: 32.8% vs real 0.99% = 33x
- [x] Workflow: 平行深度分析 v1/v2/v3/v4 + docs 稽核 + 演算法研究 (wf_95c848a9)
- [x] 校正 sim `_extend_tape` 礦物出現率 → 真實 ~1.4% pit/solid + 真實 vein shape (PMF, no 3x3)
- [x] 加 sim 密度自我量測 (spawn 1.31%, standing 量測 + fallback 模型)
- [x] gold-standard: replay v1/v2/v3/v4 on 1067 真實 board (tools/replay_real_boards.py)
- [x] 決定: 移除 v2 (real board 18.8% 超過 300ms, max 1841ms)
- [x] 移除 v2: mining_service dispatch + config_manager enum + sim_eval/replay PLANNERS + del planner.py/debug_with_image_plan.py/test
- [x] 優化1: v3 加 wall-clock deadline (453ms→262ms, 0 violations)
- [x] focused pytest 全綠 (v3 67 / v4 / shared-infra 18 / integration 4 / shovel 5 / classifier 4)
- [x] 修文檔 (game_rules.md / planner-eval skill / CLAUDE.md / INDEX.md / v2 __init__ docstring)
- [x] 寫新 analysis .md (docs/MINING_ALGORITHM_ANALYSIS.md)
- [x] Review section (見下)

## Review (2026-06-05)
全部完成並驗證 (real-board replay + sim + 82 focused tests 全綠)。

**交付物**
- 新工具 3 個: tools/analyze_mining_logs.py / replay_real_boards.py / compare_planners.py
- 校正: tools/mining_sim_eval.py (vein PMF + 密度對齊真實 1.41% pit/solid + fallback 模型 + standing density 量測)
- 移除 v2: del planner.py/debug_with_image_plan.py/test_miner_v2_planner.py + 改 dispatch/config/PLANNERS
- 優化: v3 加 230ms wall-clock deadline (real-board max 453→233ms, 0 violation)
- 文檔: CLAUDE.md / game_rules.md / planner-eval SKILL.md / docs/INDEX.md / docs/MINING_ALGORITHM_ANALYSIS.md (新)

**沒做 (有意)**
- 沒改 production default (維持 v4 — real-board 最快 + buried-pit corridor 護欄); v1 更省鏟但建議 live A/B 再翻
- 沒 re-fit v4 常數 (低 ROI + 被 test 鎖死); 列為 future
- 沒刪 miner/v2/ 整包 (classifier/service/types/visualization 是 v3/v4 共用層)
- 沒 commit (user 沒要求; 在 branch feat/dragon-realm)

**關鍵教訓**
- **(user 指正) 量 cluster 必須沿時間追蹤 pit,別用單張快照連通分量** — 3x3 跨 row 被逐步收集,單張漏判。
  tools/track_pits_replay.py 證實: 正方 1x1/2x2/3x3, 3x3 占 52% 礦格, spawn ~3.6% (非單張 standing 0.99%)。
  我曾據單張誤判「無 3x3」把 sim 改成小礦脈 → 已更正回正方 + 4% spawn (standing 驗證 ≈ 真實 0.99%)。
- sim 即使「跑得動」也可能 benchmark 錯 regime → 一定要拿真實 log/board 對標
- 子 agent 的 sim 數字可能因競態/沿用舊表而幻覺 → 以本機可重現量測為準

**校正後最終結論 (含 3x3)**: 決策不變 (移除 v2 / 保留 v1v3v4 / v4 default)。但 regime 是富 cluster
(3x3 占 52% 礦格),v4 cluster 機制有價值;v1 (A*) 仍小幅最佳 (sim score 948/cost186,精確找炸彈清 3x3)。

## 最終決策 (data-locked)
- REMOVE **v2** (real-board: 18.8% >300ms, max 1841ms, 歷史 stuck)。保留 v1/v3/v4。
- DEFAULT 維持 **v4** (real-board 最快 mean 1.1ms/max 46ms, 0 violation, 有 unseal-corridor fallback)。
- v1 = 最省鏟 (sim cost 211 vs v3 226), 0 punt, max 60ms — 最佳效率替代。
- 優化: v3 wall-clock deadline (唯一會破 300ms 的保留 planner → 修好)。
- 研究結論: realistic regime (75% no_pit, ~1% pit, 無 3x3) 下不需要全新演算法; 現有 3 個已 over-powered。v4 在 no_pit 自然退化成 greedy。建議 future: re-fit v4 const on 校正後 sim (低 ROI, 暫不做)。

## Real-board timing (1067 boards, <300ms = user 硬限制)
| planner | empty% | mean | p99 | max | >300ms |
|---|---|---|---|---|---|
| v4 | 0.28 | 1.1 | 12 | 46 | 0 |
| v1 | 0.28 | 2.5 | 22 | 60 | 0 |
| v3(fixed) | 0.28 | 3.7 | 36 | 262 | 0 |
| ~~v2~~ | 0.28 | 183 | 787 | 1841 | 201 (18.8%) |

## 關鍵實證 (real-game, all v4)
| 指標 | 真實值 | Sim 舊值 |
|---|---|---|
| pit 占全 cell | 0.99% | ~33% (33x 過高) |
| solid mix dirt/rock/pit | 68.7/29.9/1.4 | 72.8/27.2/0 (+cluster) |
| 有 pit 的 board | 24% | ~100% |
| cluster size 分布 | 63.5% 單格, 0% 3x3 | 大量 3x3 |
| no_pit 回合占比 | 75% | 極少 |
| v4 plan 時間 | mean 1.2ms / max 27ms | (sim ~75-100ms) |
| bomb 庫存 | mean 588 max 883 | 假設稀缺 (start 10) |
| drill 庫存 | mean 59 max 343 | start 10 |

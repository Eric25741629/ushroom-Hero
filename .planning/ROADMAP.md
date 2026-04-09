# ROADMAP

## Summary
- Phases: 4
- Active phase directories aligned: `01`, `02`, `03`, `04`
- Completed phases on disk: 2

| # | Phase | Goal | Requirements |
|---|-------|------|--------------|
| 1 | MuMu 模擬器管理與卡死自動重啟 | 建立 MuMu 12 模擬器控制與低成本卡死偵測/自動重啟閉環 | PHASE-01 |
| 2 | 待規劃 Phase 2 | [To be planned] | TBD |
| 3 | 讓 OCR 併發執行與節流控制 | [To be planned] | TBD |
| 4 | 雙週副本（週六/週日 20:00）自動開啟與戰鬥補給穩定化 | 建立雙週副本最小可行自動化與可追溯恢復流程 | SCH-01, STAB-02, STAB-04 |

## Phase Details

### Phase 1: MuMu 模擬器管理與卡死自動重啟（control.exe: launch/shutdown/restart/show_window/hide_window + emulator* 卡死偵測）

**Goal:** 建立 MuMu 12 模擬器控制與低成本卡死偵測/自動重啟閉環，確保 `emulator-*` 在卡死或斷線後可自動恢復。
**Requirements**: PHASE-01
**Depends on:** None
**Plans:** 3/3 plans complete

Plans:
- [x] 01-01-PLAN.md - MuMu Control + Watchdog Core
- [x] 01-02-PLAN.md - Recovery Orchestration + Observability
- [x] 01-03-PLAN.md - MuMuManager path compatibility gap closure (completed 2026-03-15)

Success criteria:
1. 已封裝 `control -v <index> launch/shutdown/restart/show_window/hide_window`，並支援 `emulator-5554=0`、`emulator-5556=1`、其餘依序遞增。
2. 卡死偵測採分層策略：L0（心跳+輕量 adb ping）、L1（可疑時才做截圖 hash）、L2（stale + (畫面凍結或 adb 連續失敗) 才判定）。
3. 若裝置已離線，流程不依賴截圖，直接走連線恢復/重啟路徑。
4. 高風險操作具操作級 timeout（如 8-12 秒），逾時先局部恢復，連續失敗才升級重啟。
5. 重啟保護已落地：cooldown + 每小時最大重啟次數，避免重啟風暴。
6. 單一裝置卡死與重啟不影響其他裝置循環，且重啟結果可在狀態與日誌中追蹤。

### Phase 2: 待規劃 Phase 2

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 1
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd:plan-phase 2 to break down)

### Phase 3: 讓 OCR 併發執行與節流控制

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 2
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd:plan-phase 3 to break down)

### Phase 4: 雙週副本（週六/週日 20:00）自動開啟與戰鬥補給穩定化

**Goal:** 建立雙週副本最小可行自動化，確保排程正確觸發、關鍵步驟具重試/逾時保護、戰鬥迴圈可安全退出，且失敗可回主畫面並有可追溯日誌。
**Requirements**: SCH-01, STAB-02, STAB-04
**Depends on:** Phase 3
**Plans:** 2/2 plans complete

Plans:
- [x] 04-01-PLAN.md - Scheduler + Step Guardrails (completed 2026-03-15)
- [x] 04-02-PLAN.md - Safe Combat Loop + Recovery Logging (completed 2026-03-15)

---
*Roadmap revised: 2026-04-09*







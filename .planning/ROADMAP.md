# ROADMAP

## Summary
- Phases: 7
- v1 requirements mapped: 25/25
- Existing validated requirements: 2

| # | Phase | Goal | Requirements |
|---|-------|------|--------------|
| 1 | 狀態機核心落地 | 建立可觀測、可控、可恢復的裝置狀態機基線 | FSM-01, FSM-02, FSM-03, FSM-04 |
| 2 | OCR 智慧運維 | 自動分析 OCR 失敗截圖、標註並每日自動訓練 | OCR-01, OCR-02, OCR-03 |
| 3 | 穩定性與恢復強化 | 讓長時掛機具備完整心跳、故障恢復、事件追蹤 | STAB-01, STAB-02, STAB-04 |
| 4 | 統一排程器 | 統一任務分派與優先級，避免飢餓並維持一致性 | SCH-01, SCH-02, SCH-03 |
| 5 | 即時控制分頁與營運指標 | 補齊分頁控制、更新提示與裝置級 KPI 可視化 | WEB-02, WEB-03, WEB-04, WEB-05, WEB-06, WEB-07, WEB-08 |
| 6 | 策略框架標準化 | 降低新增策略成本並支持執行中覆寫 | STR-01, STR-02 |
| 7 | 多主機/手機接入強化 | 強化多主機裝置識別與手機重新接入恢復 | HOST-01, HOST-02 |

## Phase Details

### Phase 1: 狀態機核心落地
Goal: 建立統一狀態模型與轉移規則，讓流程切換與異常處理可觀測。
Requirements: FSM-01, FSM-02, FSM-03, FSM-04
Success criteria:
1. 每台裝置都能回報標準狀態集合（Idle/Running/Recovering/Paused/Error）。
2. 每次狀態轉移都包含 trigger 與 timestamp。
3. 非法轉移會被阻擋並落日誌。
4. 逾時狀態可自動轉移到 Recovering 或 Error。

### Phase 01.1: MuMu 模擬器管理與卡死自動重啟（control.exe: launch/shutdown/restart/show_window/hide_window + emulator* 卡死偵測） (INSERTED)

**Goal:** 建立 MuMu 12 模擬器控制與低成本卡死偵測/自動重啟閉環，確保 `emulator-*` 在卡死或斷線後可自動恢復。
**Requirements**: PHASE-01.1
**Depends on:** Phase 1
**Plans:** 3/3 plans complete

Plans:
- [ ] 01.1-01-PLAN.md - MuMu Control + Watchdog Core
- [ ] 01.1-02-PLAN.md - Recovery Orchestration + Observability
- [x] 01.1-03-PLAN.md - MuMuManager path compatibility gap closure (completed 2026-03-15)

Success criteria:
1. 已封裝 `control -v <index> launch/shutdown/restart/show_window/hide_window`，並支援 `emulator-5554=0`、`emulator-5556=1`、其餘依序遞增。
2. 卡死偵測採分層策略：L0（心跳+輕量 adb ping）、L1（可疑時才做截圖 hash）、L2（stale + (畫面凍結或 adb 連續失敗) 才判定）。
3. 若裝置已離線，流程不依賴截圖，直接走連線恢復/重啟路徑。
4. 高風險操作具操作級 timeout（如 8-12 秒），逾時先局部恢復，連續失敗才升級重啟。
5. 重啟保護已落地：cooldown + 每小時最大重啟次數，避免重啟風暴。
6. 單一裝置卡死與重啟不影響其他裝置循環，且重啟結果可在狀態與日誌中追蹤。

### Phase 2: OCR 智慧運維
Goal: 建立 OCR 失敗資料閉環，讓辨識品質可持續進化。
Requirements: OCR-01, OCR-02, OCR-03
Success criteria:
1. OCR 失敗截圖會自動被蒐集且含 metadata。
2. 失敗樣本可自動上初始標籤供後續校正。
3. 每日訓練任務可自動執行並產生報告。

### Phase 3: 穩定性與恢復強化
Goal: 把長時掛機的故障處理從「手動救火」變成「自動恢復」。
Requirements: STAB-01, STAB-02, STAB-04
Success criteria:
1. 心跳與執行狀態可持續更新且可查詢。
2. 卡死/逾時可觸發分級恢復流程並成功回到可運行狀態。
3. 關鍵失敗與恢復結果可在日誌中追溯。

### Phase 4: 統一排程器
Goal: 將任務管理集中化，確保公平與可預期行為。
Requirements: SCH-01, SCH-02, SCH-03
Success criteria:
1. 任務以優先級、冷卻、重試規則執行。
2. 低優先任務不再長時間飢餓。
3. 多主機多實例下任務分派與狀態一致。

### Phase 5: 即時控制分頁與營運指標
Goal: 在現有網頁提供完整操控與裝置 KPI 觀測能力。
Requirements: WEB-02, WEB-03, WEB-04, WEB-05, WEB-06, WEB-07, WEB-08
Success criteria:
1. 可對單裝置啟停掛機並切換策略。
2. 網頁每次更新可提示版本與更新摘要。
3. 可查看每台裝置今日喚醒次數、平均喚醒時間、錯誤率。

### Phase 6: 策略框架標準化
Goal: 讓策略新增與切換可維護、可測試。
Requirements: STR-01, STR-02
Success criteria:
1. 新策略可透過標準介面註冊，不需改核心排程器。
2. 每台裝置可設預設策略，並允許運行時覆寫。

### Phase 7: 多主機/手機接入強化
Goal: 提升跨主機與移動裝置場景的穩定控制能力。
Requirements: HOST-01, HOST-02
Success criteria:
1. 裝置識別在多主機場景唯一且可追蹤。
2. 手機移動後可重新註冊並恢復可控狀態。

### Phase 8: 2

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 7
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd:plan-phase 8 to break down)

### Phase 9: 讓 OCR 併發執行與節流控制

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 8
**Plans:** 0 plans

Plans:
- [ ] TBD (run /gsd:plan-phase 9 to break down)

### Phase 10: 雙週副本（週六/週日 20:00）自動開啟與戰鬥補給穩定化

**Goal:** 建立雙週副本最小可行自動化，確保排程正確觸發、關鍵步驟具重試/逾時保護、戰鬥迴圈可安全退出，且失敗可回主畫面並有可追溯日誌。
**Requirements**: SCH-01, STAB-02, STAB-04
**Depends on:** Phase 9
**Plans:** 2/2 plans complete

Plans:
- [x] 10-01-PLAN.md - Scheduler + Step Guardrails (completed 2026-03-15)
- [x] 10-02-PLAN.md - Safe Combat Loop + Recovery Logging (completed 2026-03-15)

---
*Roadmap revised: 2026-03-13*







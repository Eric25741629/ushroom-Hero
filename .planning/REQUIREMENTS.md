# REQUIREMENTS

## Validated
- ✅ **STAB-03**: 單一裝置故障不會中斷其他裝置的掛機循環（existing）。
- ✅ **WEB-01**: 使用者可在網頁分頁一鍵啟動/停止全域掛機（existing）。

## v1 Requirements

### State Machine (P0)
- [ ] **FSM-01**: 每台裝置有明確狀態（Idle/Running/Recovering/Paused/Error）且可查詢。
- [ ] **FSM-02**: 每次狀態轉換需帶有觸發原因與時間戳記。
- [ ] **FSM-03**: 非法狀態轉移會被拒絕並記錄。
- [ ] **FSM-04**: 關鍵狀態逾時時可自動轉移到 Recovering 或 Error，避免卡死。

### OCR Ops & Auto-Train
- [ ] **OCR-01**: 系統可自動收集 OCR 失敗截圖並存入標準資料夾與 metadata。
- [ ] **OCR-02**: 系統可自動為 OCR 失敗截圖上初始標籤（如場景、失敗類型、信心分數區間）。
- [ ] **OCR-03**: 系統每日可排程自動訓練 OCR 模型/規則並產出訓練報告。

### Stability & Recovery
- [ ] **STAB-01**: 系統每輪都會更新裝置心跳與執行狀態，供監控與告警。
- [ ] **STAB-02**: 當流程卡住或逾時時，系統可自動執行分級恢復（重試、子流程重置、裝置重連）。
- [ ] **STAB-04**: 系統可記錄關鍵故障事件與恢復結果，供事後追蹤。

### Unified Scheduling
- [ ] **SCH-01**: 系統提供統一排程器管理掛機任務（優先級、冷卻、重試）。
- [ ] **SCH-02**: 排程器可避免任務飢餓，確保低優先任務在可接受時間內被處理。
- [ ] **SCH-03**: 排程器可在多主機多實例場景下維持任務分配與裝置狀態一致性。

### Web Control Tab & Observability
- [ ] **WEB-02**: 使用者可對單一裝置啟動/停止掛機。
- [ ] **WEB-03**: 使用者可在運行中切換掛機策略。
- [ ] **WEB-04**: 使用者可查看每台模擬器/手機的即時狀態（state、最後心跳、最近錯誤）。
- [ ] **WEB-05**: 網頁端每次更新可顯示更新提示（版本、摘要、影響範圍）。
- [ ] **WEB-06**: 網頁可查看每台裝置「今日喚醒次數」。
- [ ] **WEB-07**: 網頁可查看每台裝置「平均喚醒時間/處理時間」。
- [ ] **WEB-08**: 網頁可查看每台裝置「錯誤率」。

### Strategy Framework
- [ ] **STR-01**: 系統提供標準策略介面，允許新增策略而不修改核心排程器。
- [ ] **STR-02**: 使用者可為裝置指派預設策略並於執行中覆寫。

### Multi-Host Device Access
- [ ] **HOST-01**: 系統可區分並管理多主機上的裝置識別（host_id + device_id）。
- [ ] **HOST-02**: 手機移動後重新接入時，系統可重新註冊並恢復可控狀態。

## v2 Requirements (Deferred)
- [ ] **TAXI-01**: 新增打車流程任務模組並接入統一排程。
- [ ] **TAXI-02**: 支援掛機與打車任務的策略化切換與收益權衡。
- [ ] **STR-03**: 策略收益評分與自動策略選擇。

## Out of Scope
- 公開多租戶平台與帳號系統（目前是個人自用場景）。
- 全量微服務重構（目前以穩定既有系統為先）。
- 行動端原生控制 App（先以現有網頁分頁完成需求）。

## Traceability
| Requirement | Phase |
|-------------|-------|
| STAB-03 | Existing |
| WEB-01 | Existing |
| FSM-01 | Phase 1 |
| FSM-02 | Phase 1 |
| FSM-03 | Phase 1 |
| FSM-04 | Phase 1 |
| OCR-01 | Phase 2 |
| OCR-02 | Phase 2 |
| OCR-03 | Phase 2 |
| STAB-01 | Phase 3 |
| STAB-02 | Phase 3 |
| STAB-04 | Phase 3 |
| SCH-01 | Phase 4 |
| SCH-02 | Phase 4 |
| SCH-03 | Phase 4 |
| WEB-02 | Phase 5 |
| WEB-03 | Phase 5 |
| WEB-04 | Phase 5 |
| WEB-05 | Phase 5 |
| WEB-06 | Phase 5 |
| WEB-07 | Phase 5 |
| WEB-08 | Phase 5 |
| STR-01 | Phase 6 |
| STR-02 | Phase 6 |
| HOST-01 | Phase 7 |
| HOST-02 | Phase 7 |

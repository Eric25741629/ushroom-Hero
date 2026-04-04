# 菇勇者全自動掛機系統

## What This Is

這是一套針對「菇勇者」的全自動掛機系統，核心目標是讓掛機在多裝置、多實例環境中長時間穩定運行。系統以既有腳本為基礎持續維護與強化，並逐步擴充更多掛機策略覆蓋。你也希望把控制能力整合進現有網頁，提供即時操控與可視化狀態。

## Core Value

在多電腦多實例與可移動手機接入場景下，維持穩定、可恢復、可即時操控的自動掛機能力。

## Requirements

### Validated

- ✅ 已有多裝置/模擬器自動化基礎與腳本流程（existing）
- ✅ 已有核心掛機流程與狀態管理（`new_main_v2.py`, `bot_state.py`）（existing）
- ✅ 已有網頁控制面與 API 基礎（`control_panel_app.py`, `app.py`）（existing）
- ✅ 已可讓手機接入目前系統運作（existing）

### Active

- [ ] 優先提升現有掛機流程的長時間穩定性與自動恢復能力
- [ ] 建立可持續擴充的掛機策略框架與策略切換機制
- [ ] 在現有網頁新增即時操控分頁（啟停、切策略、查看每台實例/裝置狀態）
- [ ] 補強狀態機支援，讓流程切換、異常處理、回復路徑可觀測
- [ ] 強化多電腦多實例協作下的狀態一致性與控制可靠性

### Out of Scope

- 打車流程深度整合（目前尚未開發）— 先聚焦掛機主流程穩定化，後續再納入
- 公開化多租戶平台 — 目前以個人使用與自有設備管理為主
- 全新前端重寫 — 先在既有網頁架構上新增分頁與控制能力

## Context

目前專案屬於 brownfield 持續演進，已有大量可運作腳本與模組：
- 核心流程：`new_main_v2.py`、`game_initialization.py`、`event_manager.py`
- 狀態/配置：`bot_state.py`、`config_manager.py`、`bot_config.json`、各裝置 JSON
- 功能模組：`game_actions/`、`miner/`、`game_state/`
- 控制面：`control_panel_app.py`、`app.py`、`templates/`

你的實際使用場景是多電腦多實例（例如公司與宿舍設備），手機會隨身移動但仍希望接入同一套系統；因此架構上需要重視跨裝置穩定運作、狀態可視化與遠端可控性。

## Constraints

- **Priority**: 掛機穩定性優先於新功能擴張 — 先確保長時間可靠執行
- **Deployment**: 多電腦多實例 + 手機接入 — 必須支援跨裝置運行與控制
- **Compatibility**: 必須相容既有腳本與現有網頁 — 降低回歸與重構風險
- **Operations**: 需要即時介入能力 — 發生異常時可快速啟停與切換策略

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| v1 聚焦掛機穩定化 | 你最在意長時間可用性，先降故障與中斷率 | ⏳ Pending |
| 打車列為後續能力 | 目前尚未開發且非主要目標，避免分散 v1 資源 | ⏳ Pending |
| 採多電腦多實例 + 手機接入場景設計 | 符合你的實際使用方式，避免單機假設限制擴展 | ⏳ Pending |
| 控制面沿用既有網頁新增分頁 | 以最低成本獲得即時操作與可視化能力 | ⏳ Pending |

---
*Last updated: 2026-03-13 after initialization*

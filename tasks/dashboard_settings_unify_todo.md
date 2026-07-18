# Dashboard 設定 UI 統一 (feat/dashboard-settings-unify)

> worktree: `.worktrees/dashboard-settings-unify`  
> 基底: local main @ 96a0b331  
> 目標: 統一裝置設定 IA / 按鈕 class / 回饋 / token，不改 config payload 形狀

## P0（本批）
- [x] 命名：L1 裝置設定 / L2 任務參數 / L3 開發者選項（去掉嵌套「進階設定」）
- [x] 設定區按鈕：`btn-save/cancel/skip` 雙掛 `btn--primary/ghost/secondary`
- [x] 設定讀寫路徑 `alert` → `UI.toast`
- [x] 設定 modal 冷藍 hex → design tokens

## P1（本批）
- [x] 任務參數浮窗麵包屑「裝置設定 › 任務參數」+ 返回文案
- [x] 任務 tab 啟用數 badge（勾選計數）
- [x] task-tab / hint 色票走 tokens
- [x] 契約測試 `tests/test_dashboard_settings_ia.py`（31 含 ui_library 全過）

## 不做（YAGNI）
- 不拆 partials / 不改後端 schema
- 不合併 taskSettings 進單一 DOM（先做層級清晰；結構合併另開）
- 不整頁重寫 fly_pet / inventory

## Review
- 改動檔：`templates/dashboard.html`（設定 modal IA / 按鈕 / toast / tokens）
- 驗證：`python -m pytest tests/test_dashboard_settings_ia.py tests/test_ui_library.py -q` → 31 passed
- 殘留：雙 modal 結構仍在（已有麵包屑+返回）；卡片上其他 `btn-skip` 未全量遷移；全域 ~20 處非設定 alert 仍待後續

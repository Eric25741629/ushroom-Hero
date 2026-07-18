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

## P2（本批）
- [x] 雙 modal → 單 DOM：`#taskSettingsPanel` 嵌進 `#configModal`，view 切換
- [x] 卡片/工具列 `btn-skip/save/cancel` 全量雙掛 `btn--*`
- [x] dashboard runtime `alert(` → `UI.toast`（0 殘留）
- [x] 契約測試擴充（單 DOM / 無 bare alert / dual-mount）

## 不做（YAGNI）
- 不拆 partials / 不改後端 schema
- 不整頁重寫 fly_pet / inventory

## Review
- 改動檔：`templates/dashboard.html`、`tests/test_dashboard_settings_ia.py`
- 驗證：`python -m pytest tests/test_dashboard_settings_ia.py tests/test_ui_library.py -q` → 34 passed
- 殘留：CSS 仍保留 legacy `.btn-save` 別名（相容字串模板）；其他子頁 alert 不在本批

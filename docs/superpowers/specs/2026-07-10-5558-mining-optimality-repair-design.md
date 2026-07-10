# 5558 Mining Optimality Repair Design

## Goal

修復 5558 挖礦的四個已重現問題：web_h5 炸彈庫存被 executor OCR 誤判、v4 三步視野選到局部次佳路徑、WS-first 沒有載入頂層 mining 設定而無法使用畫面外礦點、已挖空格因低信心被判失敗。

## Design

### Inventory authority

`miner.planning.executor.get_live_item_count` 與 `miner.mining_service` 使用同一個 `read_ws_prop_counts`。web_h5 的 WS 查詢成功時直接採用 `4002/4003` 現量；只有非 web_h5 或 WS 不可用時才 fallback 到畫面 OCR。這保留 ADB 行為，也避免 executor 否決前一層已通過的 WS 檢查。

### Visible-board planning

v4 一般盤維持 depth 3；只有三步最佳解會消耗鑽頭或炸彈時，才補跑 depth 4 並以同一 objective score 比較。這避免把所有盤面的延遲與行為面一起放大，同時讓真實 5558 盤面避開 `(4,3) -> bomb` 局部解，選擇不耗炸彈且自身評分更高的四步路徑。兩次搜尋各自保留既有 250 ms deadline、8,000 node budget、branch cap 與 dominance pruning。這只改善可見盤面，不宣稱提供無界全域最優。

### Below-viewport routing

一般 `web_h5` 仍走現有 WS-first + Playwright daily pipeline，不改成 `use_ws_runner=true`。`game_actions.ws_phase.run_ws_phase` 將舊頂層 `ws_token_mining` 相容映射到巢狀 `ws_token.mining`，巢狀設定若已存在則優先。WS mining 成功後，既有 report-to-skip 映射會跳過同輪 `挖礦/Oracle`，使用 `mining_adapter.pit_directed_next` 的全地圖 Dijkstra 路線。5558 的 `allow_bomb/allow_drill` 維持目前明確設定，不擅自消耗道具。

### Dig verification

`verify_cell_empty` 先判斷分類標籤是否已是 `empty/dug_pit`，再處理信心閾值。空格語意是直接的成功證據，即使信心低也不應補點；仍是實心且低信心時維持重試。整盤完全未變的既有 `NoBoardChangeError` 防死循環行為不變。

## Error Handling

- WS inventory 查詢失敗時維持 OCR fallback。
- WS-first mining 失敗時 report 不含 `mining`，daily pipeline 照舊降級執行 Oracle。
- 搜尋碰到時間或節點上限時仍回傳目前最佳解。
- 真正未挖動的格子仍可補點並進入既有黑名單流程。

## Verification

- executor：web_h5 使用 WS，ADB/WS 失敗使用 OCR。
- planner：真實 5558 盤面預設選四步無炸彈方案。
- WS phase：頂層 mining 設定會傳給 runner，巢狀設定優先。
- verify：低信心但標籤為 empty 時成功；未變盤面仍拋 `NoBoardChangeError`。

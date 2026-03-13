# STACK 研究（菇勇者自動掛機）

## 目標情境
- 多電腦、多模擬器、多裝置（含手機）長時間運行。
- 主目標是「穩定掛機」，不是先追求新功能爆量。

## 建議保留的既有技術
- `Python 3.11+`：現有腳本生態完整、維護成本最低。
- `Flask`：你已有 `control_panel_app.py` / `app.py`，可延伸控制分頁與 API。
- `uiautomator2 + ADB`：已是裝置控制主幹，維持一致性。
- `OpenCV + OCR`：保留視覺辨識主流程，先改善準確率與回退策略。

## 建議新增/強化
- 排程層：統一任務排程器（priority queue + cooldown + retry policy）。
- 狀態層：明確 FSM（state enum + transition guard + timeout transition）。
- 通訊層：控制平面 API 標準化（裝置心跳、命令 ACK、狀態快照）。
- 儲存層：將關鍵執行狀態集中到 SQLite（現有 JSON 可保留為裝置配置）。
- 觀測層：結構化 logging（JSON logs）+ 基礎 metrics（成功率、重試率、平均循環時長）。

## 版本與套件建議（2026）
- Python: `3.11` 或 `3.12`（先以現有相容性為準）。
- Flask: `>=2.3`（保留你現有路由風格，必要時逐步升級）。
- Pydantic: `v2`（用於 API schema 與 config validation）。
- APScheduler 或自研 scheduler：若你要強可控與可追蹤，偏向自研輕量 scheduler。
- SQLAlchemy（可選）：若要管理 SQLite schema 演進可導入；否則先用輕量 DAO。

## 建議避免
- 立即重寫成微服務：目前會放大運維成本，與「先穩定」目標衝突。
- 先導入過重訊息中介（Kafka/RabbitMQ）：小規模私用場景不划算。
- 一次性替換全部 OCR/策略模組：高回歸風險，應採漸進替換。

## 主要風險
- 腳本單體持續擴大，導致修 bug 牽一髮動全身。
- 跨電腦與手機連線品質波動，造成狀態漂移。
- OCR 誤判在長時運行中累積成錯誤決策。

## 結論
- 技術方向應是「保留現有骨幹 + 補齊排程與狀態機 + 加強觀測與恢復」。

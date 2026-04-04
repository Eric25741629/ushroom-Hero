# ARCHITECTURE 研究（菇勇者自動掛機）

## 建議元件邊界
- Control Plane：網頁/API/命令入口（啟停、切策略、強制任務）。
- Scheduler：統一排程（優先級、冷卻、重試、互斥）。
- State Machine：每裝置工作狀態與轉換規則。
- Device Worker：實際執行 ADB/uiautomator2/OCR/行為腳本。
- Observability：日誌、事件流、健康檢查、警示。
- Config/Store：裝置設定、策略配置、執行快照。

## 資料與控制流
1. Web 分頁下達命令到 Control Plane API。
2. API 寫入命令佇列並更新 desired state。
3. Scheduler 依策略與優先級分派到特定 Device Worker。
4. Worker 依 FSM transition 執行步驟，回報結果與 telemetry。
5. Control Plane 聚合各裝置狀態，推送至 UI。

## 多電腦 + 手機接入設計
- 每台主機執行一個 host agent，負責本機模擬器/手機。
- 中央控制層只管理「命令與狀態」，不直接綁死單一主機。
- 使用統一裝置 ID（device_id + host_id）避免重名衝突。
- 手機移動時，以重新心跳註冊機制接管所屬 host。

## 錯誤恢復策略
- 裝置層：步驟重試 -> 子流程重置 -> 裝置重連。
- 排程層：失敗任務延遲重排，避免熱點重試風暴。
- 全域層：若主機離線，將其任務標為暫停與可接管。

## 建置順序
1. 先定義狀態機與事件模型。
2. 落地統一排程器（可先包住現有流程）。
3. 打通控制 API 與即時分頁。
4. 補觀測與異常自救。
5. 最後擴策略與進階優化。

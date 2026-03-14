# 核心業務邏輯層

## 📌 目的

整合業務邏輯的協調層，不涉及具體的遊戲操作，而是：
- 遊戲狀態管理
- 設備生命週期管理
- 業務流程協調

## 🎯 核心模塊

### 1. 狀態管理 (`state_manager.py`)
- 遊戲狀態追蹤（主頁面、獎勵、異地登錄等）
- 設備狀態（運行、暫停、停止）
- 狀態變化回調

### 2. 設備主處理器 (`device_handler.py`)
- 設備的完整生命週期
- 與 ADB 層、遊戲模塊的協調
- 錯誤恢復邏輯

## 📚 使用示例

```python
from core.device_handler import DeviceHandler

handler = DeviceHandler('emulator-5554')
handler.initialize()
handler.run()  # 主循環
```

## 🔄 流程

```
DeviceHandler
    ↓
├─ ADB 層（設備通信）
├─ 遊戲初始化（啟動遊戲）
├─ 業務模塊（農場、停車等）
└─ 狀態管理（追蹤狀態）
```

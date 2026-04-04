# 遊戲初始化模塊

## 📌 目的

處理遊戲啟動和初始化流程。包括：
- 遊戲啟動
- 頁面檢測
- 初始化邏輯處理

## 🎯 核心模塊

### 1. 啟動處理 (`startup_handler.py`)
- 啟動遊戲
- 處理各種啟動狀態
- 登錄相關邏輯

### 2. 頁面檢測 (`page_detector.py`)
- OCR 文字識別
- 遊戲頁面分類
- 狀態判斷

### 3. 已有模塊 (`game_initialization.py`)
- 已存在的頁面判斷與處理邏輯

## 📝 支持的頁面

- 隱藏（Loading）
- 獎勵（離線獎勵）
- 公告
- 購物管家
- 車位倉庫
- 異地登錄
- 未知狀態

## 📚 使用示例

```python
from game_init.startup_handler import StartupHandler

handler = StartupHandler(device, ip, easyocr_reader)
result = handler.ensure_game_ready()
```

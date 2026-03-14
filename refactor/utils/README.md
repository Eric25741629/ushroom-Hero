# 工具層

## 📌 目的

提供系統級的工具函數和配置管理。

## 🎯 核心模塊

### 1. 配置管理 (`config.py`)
- 全局配置
- 設備配置
- 常數定義

### 2. 日誌系統 (`logger.py`)
- 統一日誌記錄
- 設備日誌分檔
- 日誌級別管理

### 3. 時間工具 (`time_utils.py`)
- 時間比較
- 過期檢查
- 時區管理

## 📚 使用示例

### 配置

```python
from utils.config import Config

config = Config()
config.load_device_config('emulator-5554')
```

### 日誌

```python
from utils.logger import get_device_logger

logger = get_device_logger('emulator-5554')
logger.info("某個事件")
```

### 時間

```python
from utils.time_utils import is_expired

if is_expired(last_time, expired_time=3600):
    print("已過期")
```

## 🔗 依賴關係

```
高層模塊
    ↓
工具層
    ↓
標準庫（logging, time 等）
```

## 📝 添加新工具

1. 根據功能創建新文件（如 `string_utils.py`）
2. 在 `__init__.py` 中導出
3. 在文檔中說明用途

# ADB 設備通信層

## 📌 目的

集中管理所有與 Android ADB（Android Debug Bridge）相關的操作。
這一層是遊戲自動化的「底層基礎設施」。

## 🎯 核心功能

### 1. ADB 基礎命令 (`adb_base.py`)
- 執行 ADB 原始命令
- 錯誤處理與重試邏輯
- 命令超時管理

### 2. 設備管理 (`device_manager.py`)
- 列舉所有連接的設備
- 連接到指定設備（u2.Device）
- 設備狀態管理
- 設備清理與斷開

## 📚 使用示例

```python
from adb_layer.device_manager import DeviceManager

# 初始化管理器
manager = DeviceManager()

# 列出所有設備
devices = manager.list_devices()
print(f"找到 {len(devices)} 個設備: {devices}")

# 連接到設備
device = manager.connect('emulator-5554')

# 執行 ADB 命令
manager.execute_adb_command('shell wm density 240', 'emulator-5554')

# 檢查設備狀態
status = manager.get_device_status('emulator-5554')
print(f"設備狀態: {status}")
```

## 🔄 數據流

```
高層業務邏輯
    ↓
ADB 層（device_manager）
    ↓
ADB 基礎層（adb_base）
    ↓
系統 ADB 工具
    ↓
Android 設備
```

## ✅ 第 1 階段目標

提取以下功能到 ADB 層：

- [ ] 設備連接 (`connect_u2_with_retries`)
- [ ] 設備列舉 (`get_adb_devices`)
- [ ] ADB 命令執行 (`run_adb`)
- [ ] 應用啟動 (`start_game_by_icon`, `check_in_game`)
- [ ] 屏幕管理 (`unlock_screen`, `screenshot`)
- [ ] 應用管理 (`app_stop`, `app_start`)
- [ ] 應用克隆 (`launch_clone`)

## 🔗 依賴關係

```
adb_base.py
    ↓
device_manager.py
    ↓
其他模塊使用
```

## 📝 文件清單

| 文件 | 說明 |
|------|------|
| `adb_base.py` | ADB 命令執行的最底層 |
| `device_manager.py` | 設備連接和管理 |
| `__init__.py` | 模塊導出 |
| `README.md` | 本文件 |

## 🚀 下一步

完成 ADB 層後，其他模塊會使用 `DeviceManager` 來操作設備，
而不需要直接調用 ADB 命令。

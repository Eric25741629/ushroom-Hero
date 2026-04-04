# 快速查閱表 ⚡

## 🎯 我需要...

### 📖 了解項目結構
```
❓ 什麼是重構？
→ 讀 refactor/README.md

❓ 項目整體規劃是什麼？
→ 讀 refactor/PROJECT_STRUCTURE.md

❓ 代碼要遷移到哪裡？
→ 讀 refactor/MIGRATION_GUIDE.md
```

### 🔧 使用新 ADB 層
```
❓ 如何連接設備？
→ from refactor.adb_layer import DeviceManager
   manager = DeviceManager()
   device = manager.connect('emulator-5554')

❓ 如何執行 ADB 命令？
→ manager.execute_adb_command('emulator-5554', 'shell ...')

❓ 如何列出所有設備？
→ manager.list_all_devices()

❓ 如何設置屏幕？
→ manager.set_screen_config('emulator-5554', density=240)

❓ 詳細用法？
→ 讀 refactor/adb_layer/README.md
```

### 🎨 在特定層添加功能
```
❓ 我要加 ADB 相關代碼
→ refactor/adb_layer/

❓ 我要加業務邏輯（農場、停車等）
→ refactor/game_modules/

❓ 我要加遊戲初始化邏輯
→ refactor/game_init/

❓ 我要加狀態管理
→ refactor/core/

❓ 我要加工具函數
→ refactor/utils/

❓ 我不確定放在哪裡
→ 讀 refactor/MIGRATION_GUIDE.md
```

### 📝 找文檔
```
❓ ADB 層怎麼用？
→ refactor/adb_layer/README.md

❓ 業務模塊怎麼編寫？
→ refactor/game_modules/README.md

❓ 遊戲初始化怎麼實現？
→ refactor/game_init/README.md

❓ 核心層做什麼的？
→ refactor/core/README.md

❓ 工具函數在哪裡？
→ refactor/utils/README.md

❓ 完整的遷移步驟？
→ refactor/MIGRATION_GUIDE.md

❓ 當前進度如何？
→ refactor/COMPLETION_SUMMARY.md
```

## 📁 文件位置快查

### ADB 層（✅ 已完成）
```
refactor/adb_layer/
├── adb_base.py           ← ADB 命令執行
├── device_manager.py     ← 設備管理（主要用這個）
├── README.md             ← 使用說明
└── __init__.py
```

### 其他層（📋 計劃中）
```
refactor/
├── core/                 ← 業務協調
├── game_init/            ← 遊戲初始化  
├── game_modules/         ← 遊戲功能
└── utils/                ← 工具函數
```

## 🔗 常用代碼片段

### 初始化設備管理器
```python
from refactor.adb_layer import DeviceManager

manager = DeviceManager()
```

### 連接設備
```python
device = manager.connect('emulator-5554')
```

### 執行 ADB 命令
```python
output = manager.execute_adb_command(
    'emulator-5554',
    'shell wm density 240'
)
```

### 列出所有設備
```python
devices = manager.list_all_devices()
print(f"找到 {len(devices)} 個設備: {devices}")
```

### 設置屏幕
```python
manager.set_screen_config(
    'emulator-5554',
    density=240,
    width=540,
    height=960
)
```

### 啟動應用
```python
manager.launch_app('emulator-5554', 'com.mxdzz.tw.and')
```

### 停止應用
```python
manager.stop_app('emulator-5554', 'com.mxdzz.tw.and')
```

## 📚 文檔索引

| 文檔 | 位置 | 內容 | 閱讀時間 |
|------|------|------|---------|
| 主概覽 | `refactor/README.md` | 整體介紹和導航 | 5分 |
| 項目規劃 | `PROJECT_STRUCTURE.md` | 5層結構和目標 | 10分 |
| 遷移計劃 | `MIGRATION_GUIDE.md` | 詳細遷移步驟 | 15分 |
| 完成總結 | `COMPLETION_SUMMARY.md` | 已完成的工作 | 5分 |
| ADB 層 | `adb_layer/README.md` | 如何使用 ADB 層 | 5分 |
| 核心層 | `core/README.md` | 核心層規劃 | 5分 |
| 初始化 | `game_init/README.md` | 遊戲初始化規劃 | 5分 |
| 業務模塊 | `game_modules/README.md` | 業務模塊規劃 | 5分 |
| 工具層 | `utils/README.md` | 工具層規劃 | 5分 |

## 💡 提示

### 給新用戶
- 先讀 `README.md`，5 分鐘快速了解
- 如果想立即使用，就讀 `adb_layer/README.md`
- 如果想貢獻代碼，讀 `MIGRATION_GUIDE.md`

### 給 AI 助手
- 查看對應層的 `README.md` 了解上下文
- 查看 `MIGRATION_GUIDE.md` 確定代碼放置位置
- 查看 `adb_layer/` 的實現作為編碼風格參考

### 給開發者
- 拷貝 `adb_layer/` 的結構作為新層的模板
- 所有層都應有 `README.md` 和 `__init__.py`
- 每個文件都應有完整的文檔字符串

## ⚡ 快速命令

### Python 中導入
```python
# 最常用
from refactor.adb_layer import DeviceManager

# 如果需要基礎函數
from refactor.adb_layer import execute_adb_command, list_devices

# 如果需要異常類
from refactor.adb_layer import ADBCommandError, DeviceConnectionError
```

### 查看文檔
```python
from refactor.adb_layer import DeviceManager
help(DeviceManager)
help(DeviceManager.connect)
```

## 📞 遇到問題？

| 問題 | 解決方案 |
|------|---------|
| 不知道用什麼 | 讀 `refactor/adb_layer/README.md` |
| 連接設備失敗 | 檢查 ADB 是否在 PATH，或修改 `adb_base.py` 中的 `ADB_PATH` |
| 不知道代碼放哪 | 讀 `refactor/MIGRATION_GUIDE.md` |
| 想看使用例子 | 查看各層 `README.md` 中的示例 |
| 想貢獻代碼 | 參考 `adb_layer/` 的編碼風格 |

---

**最後更新**: 2026-01-28  
**狀態**: ✅ 第 1 階段完成  
**下一步**: 第 2 階段（遊戲初始化）

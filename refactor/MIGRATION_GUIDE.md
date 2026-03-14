# 代碼遷移指南

## 📋 現狀分析

### 當前架構問題
- `new_main_before20250514.py` 是一個 1226 行的單一大文件
- 所有邏輯混在一起，難以測試和維護
- 每次修改都要改動主文件

### 新架構優勢
- 模塊化設計，易於測試
- 清晰的職責分割
- 便於新功能添加
- AI 易於理解和協助

## 🎯 分階段遷移計劃

### 第 1 階段：ADB 層提取 ✅ 已完成

**文件位置**: `refactor/adb_layer/`

**提取內容**:
```python
# 從 adb_devices.py 和 new_main_before20250514.py 提取

execute_adb_command()      → adb_base.py
list_devices()             → adb_base.py
launch_clone()             → adb_base.py
set_screen_density()       → adb_base.py
reset_screen_settings()    → adb_base.py

DeviceManager              → device_manager.py（新）
```

**使用方式改變**:
```python
# 舊方式
from adb_operations import run_adb
run_adb('shell wm density 240', device_serial=ip)

# 新方式
from refactor.adb_layer import DeviceManager
manager = DeviceManager()
manager.set_screen_config(device_id, density=240)
```

### 第 2 階段：遊戲初始化（已有基礎）

**文件位置**: `refactor/game_init/`

**已有**: `game_initialization.py`（包含 `handle_game_startup_pages`）

**需要提取**:
- 頁面檢測邏輯
- OCR 文字處理
- 遊戲啟動邏輯

### 第 3 階段：業務模塊提取

**文件位置**: `refactor/game_modules/`

提取各個遊戲功能：

```
farming/          ← 從 farm.py
parking/          ← 從 park.py
mining/           ← 從 miner/ 和 oracle()
battle/           ← 從 new_battle.py
family/           ← 從 family.py
mission/          ← 從 Mission.py
```

**提取邏輯**:
```python
# 農場模塊示例
class FarmingHandler:
    def __init__(self, device, device_id, easyocr_reader, cnn_model):
        self.device = device
        self.device_id = device_id
        self.reader = easyocr_reader
        self.model = cnn_model
    
    def can_execute(self) -> bool:
        """檢查是否可以執行"""
        stage = self.get_stage()
        return stage == "主頁面"
    
    def execute(self) -> bool:
        """執行農業"""
        try:
            # 現有 farm() 函數的邏輯
            self.device.click(100, 200)  # 點擊農場
            time.sleep(1)
            # ... 更多操作
            return True
        except Exception as e:
            self.logger.error(f"農業失敗: {e}")
            return False
```

### 第 4 階段：核心層重構

**文件位置**: `refactor/core/`

**新建**:
```python
device_handler.py  # 設備生命週期管理
state_manager.py   # 遊戲狀態追蹤
```

**DeviceHandler 結構**:
```python
class DeviceHandler:
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.device_manager = DeviceManager()
        self.modules = {}  # 所有業務模塊
    
    def initialize(self):
        """初始化設備"""
        self.device = self.device_manager.connect(self.device_id)
        # 初始化所有模塊
    
    def run(self):
        """主循環"""
        while True:
            self.execute_cycle()
    
    def execute_cycle(self):
        """執行一個完整周期"""
        # 1. 遊戲初始化檢查
        # 2. 執行所有業務模塊
        # 3. 計算下次喚醒時間
        # 4. 睡眠
```

### 第 5 階段：主程序改造

**文件位置**: `refactor/main_refactored.py`

**對比**:
```python
# 舊的 main() - 1226 行混合邏輯

# 新的 main_refactored.py - 100 行清晰結構
from refactor.core.device_handler import DeviceHandler

def main(device_id):
    handler = DeviceHandler(device_id)
    handler.initialize()
    handler.run()

if __name__ == '__main__':
    # 多線程啟動各個設備
```

## 📊 進度追蹤表

| 階段 | 狀態 | 位置 | 預計工時 | 完成時間 |
|------|------|------|---------|---------|
| 1. ADB 層 | ✅ 完成 | `adb_layer/` | 1-2h | 現在 |
| 2. 遊戲初始化 | 📋 計劃 | `game_init/` | 30m | - |
| 3. 業務模塊 | 📋 計劃 | `game_modules/` | 2-3h | - |
| 4. 核心層 | 📋 計劃 | `core/` | 1-2h | - |
| 5. 主程序 | 📋 計劃 | `main_refactored.py` | 1-2h | - |
| **總計** | - | - | **4-8h** | - |

## 🔄 如何使用新代碼

### 立即可用（第 1 階段完成）

```python
# 在任何地方替換舊的 adb 調用

# 舊
from adb_devices import run_adb
run_adb('devices')

# 新
from refactor.adb_layer import DeviceManager
manager = DeviceManager()
manager.list_all_devices()
```

### 逐步遷移

1. 保持舊文件不動
2. 新功能使用新代碼
3. 舊功能逐漸遷移
4. 最後完全替換

## 📝 文檔索引

| 文檔 | 位置 | 說明 |
|------|------|------|
| 項目結構 | `refactor/PROJECT_STRUCTURE.md` | 整體規劃 |
| ADB 層 | `refactor/adb_layer/README.md` | ADB 操作 |
| 核心層 | `refactor/core/README.md` | 業務協調 |
| 遊戲初始化 | `refactor/game_init/README.md` | 遊戲啟動 |
| 業務模塊 | `refactor/game_modules/README.md` | 業務邏輯 |
| 工具層 | `refactor/utils/README.md` | 公共工具 |
| **本文** | `refactor/MIGRATION_GUIDE.md` | 遷移指南 |

## 🚀 下一步行動

**立即可做**:
1. ✅ 審查 ADB 層代碼（`adb_layer/`）
2. ⏭️ 驗證 `DeviceManager` 能否連接設備
3. ⏭️ 在小規模測試中使用新代碼

**確認無誤後**:
1. 開始第 2 階段（遊戲初始化）
2. 逐步遷移業務模塊
3. 最終整合為新主程序

## 🤝 與 AI 協作

現在 AI 可以更容易地：
- 理解代碼結構
- 修改單個模塊而不影響其他
- 建議新功能應該放在哪裡
- 幫助測試每個模塊

只需告訴 AI：
> 我需要在 `refactor/game_modules/farming/` 中添加新功能
> 
> 或
>
> 幫我測試 `refactor/adb_layer/device_manager.py` 中的連接邏輯

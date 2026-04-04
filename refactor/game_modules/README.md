# 遊戲業務模塊層

## 📌 目的

獨立的遊戲業務功能模塊，每個模塊一個文件夾。
所有模塊都可獨立測試和驗證。

## 🎯 模塊清單

### 1. 農場模塊 (`farming/`)
- 種植和收割
- 資源管理

### 2. 停車模塊 (`parking/`)
- 停車邏輯
- 停車位管理

### 3. 挖礦模塊 (`mining/`)
- 礦點操作
- 資源採集

### 4. 戰鬥模塊 (`battle/`)
- 戰鬥執行
- 敵人管理

### 5. 家族模塊 (`family/`)
- 家族相關操作
- 商店購買

### 6. 任務模塊 (`mission/`)
- 日常任務
- 任務完成

## 📚 模塊結構

每個模塊都遵循相同的結構：

```
{module_name}/
├── __init__.py         # 模塊入口
├── {module_name}.py    # 核心實現
├── config.py          # 配置（可選）
└── README.md          # 說明文檔
```

## 🔗 依賴關係

```
業務模塊
    ↓
遊戲初始化 + ADB 層
    ↓
設備管理
    ↓
ADB 基礎
```

## 📝 模塊通用接口

所有模塊應實現統一的接口：

```python
class ModuleHandler:
    def __init__(self, device, device_id, easyocr_reader, cnn_model):
        pass
    
    def can_execute(self) -> bool:
        """檢查是否可以執行"""
        pass
    
    def execute(self) -> bool:
        """執行操作，返回成功或失敗"""
        pass
```

## 🚀 創建新模塊

1. 在 `game_modules/` 下建立新文件夾
2. 複製 `README.md` 並修改
3. 實現 `ModuleHandler` 接口
4. 添加到主程序的模塊清單

## 📊 優先級

按照提取的優先級：

1. ⚡ 農場 + 停車（最常用）
2. ⚡ 挖礦（需要 ADB）
3. 戰鬥、家族、任務

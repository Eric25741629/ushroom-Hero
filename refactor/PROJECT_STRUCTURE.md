# 菇勇者全自動掛機 - 項目重構指南

## 📁 項目結構

```
refactor/
├── adb_layer/           # ADB 設備通信層（第 1 優先級）
│   ├── adb_base.py     # ADB 基礎命令封裝
│   ├── device_manager.py  # 設備連接管理
│   └── README.md       # ADB 層說明
│
├── core/                # 核心業務邏輯
│   ├── state_manager.py   # 遊戲狀態管理
│   ├── device_handler.py  # 設備主處理器
│   └── README.md       # 核心層說明
│
├── game_init/           # 遊戲初始化模塊（已有 game_initialization.py）
│   ├── startup_handler.py  # 遊戲啟動邏輯
│   ├── page_detector.py    # 頁面偵測
│   └── README.md       # 遊戲初始化說明
│
├── game_modules/        # 遊戲功能模塊
│   ├── farming/         # 農場模塊
│   ├── parking/         # 停車模塊
│   ├── mining/          # 挖礦模塊
│   ├── battle/          # 戰鬥模塊
│   ├── family/          # 家族模塊
│   ├── mission/         # 任務模塊
│   └── README.md       # 遊戲模塊說明
│
├── utils/               # 工具函數層
│   ├── config.py        # 配置管理
│   ├── logger.py        # 日誌系統
│   ├── time_utils.py    # 時間工具
│   └── README.md       # 工具層說明
│
└── main_refactored.py  # 重構後的主程序
```

## 🎯 重構階段

### ✅ 第 1 階段：ADB 層提取（當前進行中）
**目標**：將所有 ADB 命令集中在一個地方

- 提取 `adb_devices.py` 中的所有 ADB 操作
- 建立統一的設備連接管理
- 建立設備狀態追蹤

### 📋 第 2 階段：遊戲初始化邏輯
- 整合 `game_initialization.py`（已存在）
- 分離頁面檢測邏輯
- 分離遊戲啟動邏輯

### 📋 第 3 階段：業務邏輯模塊化
- 農場、停車、挖礦、戰鬥等各自獨立模塊
- 每個模塊有清晰的輸入輸出

### 📋 第 4 階段：主程序改造
- 使用事件驅動而不是時間驅動
- 支持網站控制

## 🔍 每個目錄的說明

每個目錄都包含：
- `README.md` - 詳細說明
- `__init__.py` - 模塊初始化
- 具體實現文件

## 🔗 文件關係圖

```
main_refactored.py
    ↓
device_handler.py (核心協調)
    ↓
├─ adb_layer/  (設備通信)
├─ game_init/  (遊戲初始化)
└─ game_modules/ (業務邏輯)
    ├─ farming
    ├─ parking
    ├─ mining
    ├─ battle
    ├─ family
    └─ mission
```

## 📝 編碼規範

- 每個模塊獨立可測試
- 清晰的文檔說明
- 類型註解（Type hints）
- 日誌記錄每個操作

## ⏱️ 預計時間表

- 第 1 階段（ADB 層）: 1-2 小時
- 第 2 階段（遊戲初始化）: 30 分鐘
- 第 3 階段（業務模塊）: 2-3 小時
- 第 4 階段（主程序）: 1-2 小時

**總計**: 4-8 小時

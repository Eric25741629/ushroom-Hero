# 菇勇者全自動掛機 - 代碼重構完整指南

## 📚 文檔導航

🚀 **新用戶/AI 助手必讀**:
1. **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** - 了解整體結構
2. **[MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)** - 了解遷移計劃
3. **[adb_layer/README.md](adb_layer/README.md)** - 了解 ADB 層（第 1 階段 ✅）

📂 **各層文檔**:
- [adb_layer/](adb_layer/) - ADB 設備通信層 ✅ **已完成**
- [core/](core/) - 核心業務邏輯層 📋 計劃中
- [game_init/](game_init/) - 遊戲初始化模塊 📋 計劃中
- [game_modules/](game_modules/) - 遊戲業務模塊 📋 計劃中
- [utils/](utils/) - 工具函數層 📋 計劃中

## 🎯 快速概覽

### 重構的核心思想

```
舊架構（混亂）          新架構（清晰）
───────────────────    ─────────────────
new_main.py            main_refactored.py
  ├─ ADB 命令 ────────→   adb_layer/
  ├─ 業務邏輯 ────────→   game_modules/
  ├─ 初始化 ──────────→   game_init/
  ├─ 狀態管理 ────────→   core/
  └─ 工具函數 ────────→   utils/
```

### 已完成的工作

✅ **第 1 階段：ADB 層提取**
- `adb_layer/adb_base.py` - ADB 命令執行基礎
- `adb_layer/device_manager.py` - 設備連接管理
- 所有 ADB 相關功能已集中

**立即可用**:
```python
from refactor.adb_layer import DeviceManager

manager = DeviceManager()
device = manager.connect('emulator-5554')
```

### 計劃中的工作

📋 **第 2-5 階段**（按順序）:
1. 遊戲初始化邏輯提取
2. 業務模塊分散（農場、停車、挖礦等）
3. 核心業務協調層
4. 主程序改造

## 📁 文件夾結構

```
refactor/
├── README.md                   # 本文件
├── PROJECT_STRUCTURE.md        # 項目整體規劃
├── MIGRATION_GUIDE.md          # 遷移指南
│
├── adb_layer/                  # ✅ ADB 設備通信層
│   ├── README.md               # ADB 層說明
│   ├── adb_base.py             # ADB 命令執行
│   ├── device_manager.py       # 設備管理
│   └── __init__.py
│
├── core/                       # 📋 核心業務邏輯層
│   ├── README.md               # 核心層說明
│   ├── state_manager.py        # 狀態管理（待）
│   ├── device_handler.py       # 設備協調（待）
│   └── __init__.py
│
├── game_init/                  # 📋 遊戲初始化模塊
│   ├── README.md               # 遊戲初始化說明
│   ├── startup_handler.py      # 遊戲啟動（待）
│   ├── page_detector.py        # 頁面檢測（待）
│   └── __init__.py
│
├── game_modules/               # 📋 遊戲業務模塊
│   ├── README.md               # 業務模塊說明
│   ├── farming/                # 農場模塊（待）
│   ├── parking/                # 停車模塊（待）
│   ├── mining/                 # 挖礦模塊（待）
│   ├── battle/                 # 戰鬥模塊（待）
│   ├── family/                 # 家族模塊（待）
│   ├── mission/                # 任務模塊（待）
│   └── __init__.py
│
├── utils/                      # 📋 工具函數層
│   ├── README.md               # 工具層說明
│   ├── config.py               # 配置管理（待）
│   ├── logger.py               # 日誌系統（待）
│   ├── time_utils.py           # 時間工具（待）
│   └── __init__.py
│
└── main_refactored.py          # 📋 重構後的主程序（待）
```

## 🔄 如何使用本重構

### 對於開發者

1. **閱讀流程**
   - 先讀 `PROJECT_STRUCTURE.md` 了解整體
   - 再讀 `MIGRATION_GUIDE.md` 了解遷移計劃
   - 最後讀各層的 `README.md`

2. **使用新代碼**
   ```python
   # 可以立即使用第 1 階段的代碼
   from refactor.adb_layer import DeviceManager
   
   manager = DeviceManager()
   devices = manager.list_all_devices()
   ```

3. **貢獻新代碼**
   - 查看對應層的 README.md
   - 按照層的規範編寫代碼
   - 添加單元測試

### 對於 AI 助手

當用戶要求幫助時：

1. **理解請求**
   > "幫我在農場模塊中添加新功能"
   
   ✅ 查看 `refactor/game_modules/README.md`

2. **定位正確位置**
   > "我需要修改設備連接邏輯"
   
   ✅ 查看 `refactor/adb_layer/device_manager.py`

3. **提供上下文**
   > "幫我測試 DeviceManager"
   
   ✅ 直接查看 `refactor/adb_layer/device_manager.py`

4. **建議新功能位置**
   > "我想添加事件系統"
   
   ✅ 應該在 `refactor/core/` 中添加

## 📊 進度統計

| 層級 | 狀態 | 完成度 | 文檔 | 代碼 |
|------|------|--------|------|------|
| ADB 層 | ✅ 完成 | 100% | ✅ | ✅ |
| 核心層 | 📋 計劃 | 0% | ⏳ | ❌ |
| 遊戲初始化 | 📋 計劃 | 0% | ⏳ | ❌ |
| 業務模塊 | 📋 計劃 | 0% | ⏳ | ❌ |
| 工具層 | 📋 計劃 | 0% | ⏳ | ❌ |
| 主程序 | 📋 計劃 | 0% | ⏳ | ❌ |

## 🎓 學習路徑

### 初級（了解結構）
1. 讀 `PROJECT_STRUCTURE.md`
2. 查看各層的 `README.md`
3. 理解文件夾組織

### 中級（使用代碼）
1. 研究 `adb_layer/device_manager.py`
2. 理解 `DeviceManager` 如何工作
3. 在小測試中嘗試使用

### 高級（擴展代碼）
1. 遵循 `MIGRATION_GUIDE.md`
2. 在相應層中添加新模塊
3. 編寫適當的文檔和測試

## 🚀 下一步

### 立即可做
- [ ] 讀完所有 README 文檔
- [ ] 驗證 `DeviceManager` 能否正常工作
- [ ] 用新 API 替換舊代碼中的一個 ADB 調用

### 明天要做
- [ ] 開始第 2 階段（遊戲初始化）
- [ ] 提取頁面檢測邏輯
- [ ] 建立 `game_init/page_detector.py`

### 本週要做
- [ ] 完成第 2-3 階段（業務模塊分散）
- [ ] 建立所有業務模塊框架
- [ ] 驗證模塊間的通信

### 本月要做
- [ ] 完成第 4-5 階段（核心層和主程序）
- [ ] 完全替換舊的 `new_main_before20250514.py`
- [ ] 實現事件驅動系統
- [ ] 建立網站控制接口

## 📞 獲取幫助

如果在使用重構代碼時遇到問題：

1. **查閱對應層的 README**
   > 問題在 ADB 層？→ 查看 `adb_layer/README.md`

2. **查看代碼中的文檔字符串**
   ```python
   from refactor.adb_layer import DeviceManager
   help(DeviceManager.connect)
   ```

3. **查閱遷移指南**
   > 不知道代碼應該放在哪？→ 查看 `MIGRATION_GUIDE.md`

4. **聯繫 AI 助手**
   > 描述你的需求，AI 會指導你

## 📝 更新日誌

### 2026-01-28
- ✅ 建立完整的重構框架
- ✅ 完成 ADB 層提取和文檔
- ✅ 創建項目結構和遷移指南

---

**狀態**: 第 1 階段完成，準備進入第 2 階段 ✅

**預計完成**: 4-8 小時內（分階段進行）

# OpenGold V2 - 神燈開裝備模組重構版

保留原有判斷邏輯，改善架構與可維護性。

## 主要改進

1. **職責分離**：將原本 1400+ 行的單一檔案拆分為 8 個職責明確的模組
2. **配置集中化**：所有可調整參數集中在 `OpenGoldConfig`
3. **自動偵測連閃裝備**：透過階段名稱自動判斷
4. **截圖記錄**：開神燈過程自動記錄截圖（上限 100 張）
5. **OCR 不完整閾值可配置**：`skip_incomplete_limit` 可調整
6. **無全域狀態**：使用 `LampState` 類別管理狀態

## 快速開始

```python
from opengold_v2 import LampService, OpenGoldConfig
import uiautomator2 as u2

# 連接裝置
d = u2.connect('7fe98fc6')

# 使用預設配置
service = LampService(d)

# 或自訂配置
config = OpenGoldConfig(
    skip_incomplete_limit=5,      # OCR 不完整最多跳過 5 次
    screenshot_max_files=200,      # 截圖上限 200 張
)
service = LampService(d, config=config)

# 執行開神燈
service.run(times=-1, is_compare=True)  # -1 表示無限執行
```

## 模組說明

| 模組 | 職責 |
|------|------|
| `config.py` | 配置管理，所有可調整參數 |
| `models.py` | 資料類別定義 |
| `ocr_parser.py` | OCR 結果解析 |
| `skill_evaluator.py` | 詞條比較邏輯 |
| `screenshot_logger.py` | 截圖記錄（上限 100 張）|
| `device_detector.py` | 自動偵測連閃裝備 |
| `ui_controller.py` | 遊戲 UI 操作 |
| `lamp_service.py` | 主流程協調器 |

## 配置檔案

可將配置儲存為 JSON：

```python
from opengold_v2 import OpenGoldConfig

# 儲存配置
config = OpenGoldConfig(skip_incomplete_limit=5)
config.save_to_file('opengold_v2/config.json')

# 載入配置
config = OpenGoldConfig.from_file('opengold_v2/config.json')
```

## 與舊版差異

| 項目 | 舊版 | V2 |
|------|------|-----|
| 狀態管理 | `globals()['ocr_skip_count']` | `LampState` 類別 |
| 連閃偵測 | 參數傳入 | 自動偵測 |
| 截圖記錄 | 不完整時才記錄 | 全程記錄（上限 100 張）|
| 配置 | 散佈各處 | 集中在 `OpenGoldConfig` |
| 測試 | 困難 | 各模組可獨立測試 |

## 注意事項

1. **ROI 座標**：目前沿用舊版座標，未來可透過配置調整
2. **截圖資料夾**：預設為 `opengold_v2/screenshots`，會自動建立
3. **向後相容**：舊版 `Open_gold_paddle_ocr.py` 仍可正常使用

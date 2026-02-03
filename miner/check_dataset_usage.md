# check_dataset.py 使用說明

## 功能
使用訓練好的 CNN 模型檢查資料集,找出低信心度和預測錯誤的樣本。

## 使用方式

### 1. 檢查整個資料集 (多類別模式)
檢查 `dataset/mines` 下所有類別的圖片:

```bash
python check_dataset.py
```

如果要移動問題檔案:
```bash
python check_dataset.py --move-files
```

### 2. 檢查單一類別資料夾 (自動檢測)
**程式會自動檢測資料夾類型,不需要額外參數!**

檢查特定類別資料夾,例如 `dataset/mines/dirt`:

```bash
# 簡單!只需指定路徑
python check_dataset.py --dataset-dir "dataset/mines/dirt" --move-files
```

或檢查其他類別:
```bash
python check_dataset.py --dataset-dir "dataset/mines/empty" --move-files
python check_dataset.py --dataset-dir "dataset/mines/rock" --move-files
```

程式會:
1. ✅ **自動檢測**資料夾內有圖片 → 單一類別模式
2. ✅ **自動推斷**類別名稱(從資料夾名稱)
3. ✅ **在資料夾內**創建子資料夾分類錯誤樣本

**這會在 `dirt` 資料夾內直接創建子資料夾分類錯誤樣本:**
```
dataset/mines/dirt/
├── empty/              # dirt 被誤判為 empty 的圖片
├── rock/               # dirt 被誤判為 rock 的圖片
├── dug_pit/            # dirt 被誤判為 dug_pit 的圖片
├── 正常圖片.png         # 預測正確的圖片(留在原位,包含低信心度的)
└── 正常圖片2.png
```

**注意:** 低信心度但預測正確的樣本**不會被移動**,會留在原位。

### 3. 參數說明

- `--dataset-dir`: 資料集路徑
  - 多類別模式: `dataset/mines` (包含多個類別子資料夾)
  - 單一類別模式: `dataset/mines/dirt` (單一類別資料夾)
  - **程式會自動檢測類型**

- `--threshold`: 統計用的信心度閾值 (預設: 0.99)
  - 用於統計和報告

- `--move-threshold`: 移動檔案的信心度閾值 (預設: 0.9)
  - 目前低信心度樣本不會被移動,此參數僅用於統計
  - 只有預測錯誤的樣本會被移動

- `--move-files`: 是否真的移動檔案
  - 不加此參數: 只顯示統計,不移動檔案
  - 加此參數: 真的移動檔案

- `--model-path`: 模型檔案路徑 (預設: `checkpoints/best.pth`)

- `--output-dir`: 報告輸出目錄 (預設: `dataset/low_confidence`)

- `--error-dir`: 錯誤樣本輸出目錄 (預設: `dataset/error`)
  - 只在多類別模式時使用

## 輸出結果

### 多類別模式的資料夾結構

#### 錯誤樣本
```
dataset/error/
├── dirt_as_empty/          # dirt 被誤判為 empty
│   └── 圖片檔案.png
├── empty_as_dug_pit/       # empty 被誤判為 dug_pit
│   └── 圖片檔案.png
```

#### 低信心度樣本
```
dataset/low_confidence/
├── dirt/                   # dirt 類別的低信心度樣本
│   └── 圖片檔案.png
├── rock/                   # rock 類別的低信心度樣本
│   └── 圖片檔案.png
```

### 單一類別模式的資料夾結構

**只移動預測錯誤的樣本,在源資料夾內按預測類別分類:**
```
dataset/mines/dirt/          # 原始 dirt 資料夾
├── empty/                   # 被誤判為 empty 的圖片
│   └── 圖片1.png
├── rock/                    # 被誤判為 rock 的圖片
│   └── 圖片2.png
├── dug_pit/                 # 被誤判為 dug_pit 的圖片
│   └── 圖片3.png
├── 圖片4.png                # 預測正確的圖片(留在原位)
└── 圖片5.png                # 低信心度但預測正確的圖片(也留在原位)
```

**注意:** 
- ✅ **只移動預測錯誤的樣本**
- ✅ **低信心度但預測正確的樣本不移動**,留在原位

### 報告檔案
- `dataset/low_confidence/report.txt`: 詳細的檢查報告

## 範例

### 檢查所有類別
```bash
# 只看統計,不移動檔案
python check_dataset.py

# 移動問題檔案
python check_dataset.py --move-files --move-threshold 0.8
```

### 檢查特定類別 (自動模式)
```bash
# 檢查 dirt 類別 - 簡單!
python check_dataset.py --dataset-dir "dataset/mines/dirt" --move-files

# 檢查 rock 類別
python check_dataset.py --dataset-dir "dataset/mines/rock" --move-files

# 檢查 empty 類別
python check_dataset.py --dataset-dir "dataset/mines/empty" --move-files

# 使用完整路徑
python check_dataset.py --dataset-dir "A:\菇勇者全自動掛機\miner\dataset\mines\empty" --move-files
```

## 注意事項

1. **自動檢測資料夾類型**
   - 有圖片檔案 → 單一類別模式(從資料夾名稱推斷類別)
   - 只有子資料夾 → 多類別模式
   - 不需要手動指定!

2. **只移動預測錯誤的樣本**
   - 低信心度但預測正確的樣本不會被移動
   - 只有真正預測錯誤的才會被分類

3. **檔案會被移動,不是複製**
   - 原始資料夾中的錯誤樣本會被移走
   - 建議先備份資料

4. **檔名保持不變**
   - 移動的檔案保持原始檔名
   - 不會加入信心度等資訊

5. **自動處理同名檔案**
   - 如果目標位置已有同名檔案,會自動加上序號 `_1`, `_2` 等

6. **類別名稱自動推斷**
   - 單一類別模式時,類別名稱取自資料夾名稱
   - 例如: `dataset/mines/dirt` → 類別為 `dirt`
   - 確保資料夾名稱與模型訓練時的類別一致

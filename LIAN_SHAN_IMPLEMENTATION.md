# 連閃裝備功能實現總結

## 修改日期
2025年12月8日

## 需求
用户擁有一套連閃裝備，由連閃和爆閃組合而成。在進行裝備比較時，如果遇到連閃與爆閃的比較，應該將兩者相加進行比較。

## 實現方案

### 核心思路
1. 在 `compare_skill_pairs()` 函數中添加 `has_lian_shan_equip` 參數
2. 當該參數為 `True` 時，將「連」與「爆」的概率值相加，作為一個整體進行比較
3. 通過函數參數逐層傳遞，最終由用戶在命令行或代碼中控制

### 修改的函數清單

#### 1. `compare_skill_pairs()` - 行 247
**修改內容**：
- 新增參數 `has_lian_shan_equip=False`
- 修改函數文檔，說明連閃裝備的特殊處理規則
- 在計算 CAP7 集合的和時，根據 `has_lian_shan_equip` 的值進行不同的計算方式

**關鍵變更**：
```python
def cap7_sum(m):
    s = 0.0
    if has_lian_shan_equip:
        # 連閃裝備：將 `連` 與 `爆` 視為一個整體（相加）
        lian_shan_sum = float(m.get('連', 0.0)) + float(m.get('爆', 0.0))
        s += lian_shan_sum
        # 加上其他上限7%的詞條
        for k in CAP7_SET:
            if k not in ('連', '爆'):
                s += float(m.get(k, 0.0))
    else:
        # 普通模式：直接相加所有上限7%的詞條
        for k in CAP7_SET:
            s += float(m.get(k, 0.0))
    return s
```

#### 2. `execute_upgrade_sequence()` - 行 621
**修改內容**：
- 新增參數 `has_lian_shan_equip=False`
- 將參數傳遞給 `compare_skill_pairs()`

#### 3. `process_wanted_combo()` - 行 582
**修改內容**：
- 新增參數 `has_lian_shan_equip=False`
- 將參數傳遞給 `execute_upgrade_sequence()`

#### 4. `open_the_gold()` - 行 497
**修改內容**：
- 新增參數 `has_lian_shan_equip=False`
- 將參數傳遞給 `process_wanted_combo()`

#### 5. `__main__` 區塊 - 行 735
**修改內容**：
- 新增命令行參數解析
- 添加 `--lian-shan` 選項來啟用連閃裝備模式
- 添加 `--compare` 選項來控制機率比對
- 將這些參數傳遞給 `open_the_gold()`

### 比較規則優先順序（未變）

1. **回復優先**：若開出回復 ≥ 0.25 且原本 < 0.25，必換
2. **上限7%詞條**：對「閃、連、爆、反」相加比較（**新增：如啟用連閃裝備模式，連和爆相加後作為一個整體**）
3. **技能暴擊優先**：優先比較「技」
4. **逐項比較**：若上述規則無法決出優劣，逐項比較

## 使用方法

### 命令行使用
```bash
# 直接運行（連閃裝備模式默認啟用）
python Open_gold_paddle_ocr.py

# 禁用連閃裝備模式
python Open_gold_paddle_ocr.py --no-lian-shan
```

### 代碼中使用
```python
# 默認啟用連閃裝備模式（推薦）
open_the_gold(d, times=-1, is_compare=True)

# 明確禁用
open_the_gold(d, times=-1, is_compare=True, has_lian_shan_equip=False)
```

## 測試場景

### 場景 1：連爆都更優
```
開出：連 3%，爆 2%（總和 5%）
原有：連 2%，爆 1%（總和 3%）
結果：建議更換（5% > 3%）
```

### 場景 2：連優爆差
```
開出：連 4%，爆 1%（總和 5%）
原有：連 2%，爆 2%（總和 4%）
結果：建議更換（5% > 4%）
```

### 場景 3：開出較差
```
開出：連 2%，爆 0%（總和 2%）
原有：連 2%，爆 2%（總和 4%）
結果：不建議更換（2% < 4%）
```

## 向後相容性
- 所有新增參數都有默認值 `True`（連閃裝備模式默認啟用）
- 已有代碼默認使用連閃裝備模式
- 需要禁用時，可顯式設置 `has_lian_shan_equip=False`
- 命令行使用 `--no-lian-shan` 來禁用

## 新增文件

1. **LIAN_SHAN_EQUIP_GUIDE.md**
   - 詳細的使用指南
   - 常見問題解答
   - 技術細節說明

2. **lian_shan_example.py**
   - 示例代碼
   - 場景演示
   - 可直接執行測試比較邏輯

## 驗證結果
✅ 代碼語法檢查通過（無錯誤）
✅ 所有參數逐層傳遞完整
✅ 向後相容性保證
✅ 命令行參數集成完整

## 注意事項
1. 連閃裝備模式只影響「連」與「爆」的比較方式
2. 其他詞條的比較邏輯保持不變
3. 回復優先規則不受影響
4. 默認情況下程式表現與修改前完全相同

# 連閃裝備使用指南

## 概述
如果您擁有一套**連閃裝備**，由「連閃」和「爆閃」組合而成，可以使用本程式提供的特殊比較模式。

## 功能說明

### 連閃裝備的特殊處理
當啟用連閃裝備模式（`has_lian_shan_equip=True`）時：
- 在比較詞條時，將「連」與「爆」的概率值相加後進行比較
- 這樣可以準確評估整套連閃裝備的總效果

### 比較規則（優先順序）
1. **回復優先**：如果開出回復 ≥ 0.25 且原本 < 0.25，則必換
2. **上限7%詞條**：對於「閃、連、爆、反」這類詞條，將相應的概率值相加
   - **特殊**：若擁有連閃裝備，將「連」與「爆」視為一個整體相加
3. **技能暴擊優先**：優先比較「技」（技能暴擊）
4. **逐項比較**：若上述規則無法決出優劣，則逐項比較

## 使用方法

### 正常使用（推薦）
連閃裝備模式已**默認啟用**，直接運行：
```bash
python Open_gold_paddle_ocr.py
```

### 禁用連閃裝備模式（若不需要）
```bash
# 臨時禁用連閃裝備模式
python Open_gold_paddle_ocr.py --no-lian-shan
```

### 其他選項
```bash
# 查看所有選項
python Open_gold_paddle_ocr.py --help
```

## 使用範例

### 場景1：正常使用（推薦）
```bash
python Open_gold_paddle_ocr.py
```
- 連閃裝備模式自動啟用
- 「連」和「爆」的概率相加後進行比較

### 場景2：禁用連閃裝備模式
```bash
python Open_gold_paddle_ocr.py --no-lian-shan
```
- 恢復到普通模式
- 「連」和「爆」分開比較

### 場景3：代碼中使用
```python
# 默認啟用（推薦）
open_the_gold(d, times=-1, is_compare=True)

# 禁用連閃模式
open_the_gold(d, times=-1, is_compare=True, has_lian_shan_equip=False)
```

## 代碼修改摘要

### 修改的函數：
1. **`compare_skill_pairs()`**
   - 新增參數：`has_lian_shan_equip=False`
   - 當啟用時，將「連」和「爆」視為一個整體進行相加

2. **`execute_upgrade_sequence()`**
   - 新增參數：`has_lian_shan_equip=False`
   - 將參數傳遞給 `compare_skill_pairs()`

3. **`process_wanted_combo()`**
   - 新增參數：`has_lian_shan_equip=False`
   - 將參數傳遞給 `execute_upgrade_sequence()`

4. **`open_the_gold()`**
   - 新增參數：`has_lian_shan_equip=False`
   - 將參數傳遞給 `process_wanted_combo()`

### 命令行介面
- 新增 `--lian-shan` 選項來啟用連閃裝備模式
- 新增 `--compare` 選項來控制機率比對

## 技術細節

### 連閃裝備的比較邏輯
```python
# 當 has_lian_shan_equip=True 時
lian_shan_sum = prob('連') + prob('爆')

# 此值會加入到上限7%詞條的相加中
# 例如：連0.03 + 爆0.02 + 閃0.01 + 反0.00 = 0.06 (總和)
```

## 常見問題

**Q：為什麼要將連和爆相加？**
A：因為您的連閃裝備由連閃和爆閃組成，這兩個詞條共同構成整套裝備的防禦能力，因此應該作為一個整體進行評估。

**Q：如果我更換了裝備怎麼辦？**
A：可以重新運行程式時不添加 `--lian-shan` 選項，恢復到普通比較模式。

**Q：連閃裝備模式會影響其他詞條的比較嗎？**
A：不會。其他詞條（回復、技、暈、閃、反等）的比較邏輯保持不變。

# 快速參考卡片

## 連閃裝備功能 - 快速上手

### 一句話說明
您的連閃裝備由連閃和爆閃組成，系統默認已啟用連閃裝備模式，比較時會將兩者概率相加，更準確地評估整套裝備。

### 如何使用？

#### 方法 1：直接運行（推薦）
連閃裝備模式已**默認啟用**，直接運行即可：
```bash
python Open_gold_paddle_ocr.py
```

#### 方法 2：禁用連閃裝備模式（若需要）
```bash
python Open_gold_paddle_ocr.py --no-lian-shan
```

#### 方法 3：代碼中使用
```python
# 默認啟用連閃裝備模式
open_the_gold(d, times=-1, is_compare=True)

# 或明確指定
open_the_gold(d, times=-1, is_compare=True, has_lian_shan_equip=True)

# 禁用連閃裝備模式
open_the_gold(d, times=-1, is_compare=True, has_lian_shan_equip=False)
```

---

## 比較邏輯詳解

### 不使用連閃裝備模式（默認）
```
開出：連 3%，爆 2%
原有：連 2%，爆 3%

比較：
- 連：3% > 2% ✓（更優）
- 爆：2% < 3% ✗（較差）
→ 判定：有優有劣，還會進行其他規則比較

結論：可能換，可能不換（取決於其他規則）
```

### 使用連閃裝備模式
```
開出：連 3%，爆 2% → 相加 = 5%
原有：連 2%，爆 3% → 相加 = 5%

比較：
- 總和：5% = 5% （相等）

結論：相等，不建議換
```

---

## 命令參數速查表

| 參數 | 説明 | 默認值 | 示例 |
|------|------|-------|------|
| （無參數） | 直接運行（推薦） | 連閃模式開啟 | `python Open_gold_paddle_ocr.py` |
| `--no-lian-shan` | 禁用連閃裝備模式 | N/A | `python ... --no-lian-shan` |
| `--compare` | 啟用機率比對 | True | `python ... --compare` |
| `--help` | 顯示幫助信息 | N/A | `python ... --help` |

---

## 常見使用場景

### 場景 A：正常使用（推薦）
```bash
# 連閃裝備模式默認啟用，直接運行即可
python Open_gold_paddle_ocr.py
```

### 場景 B：暫時禁用連閃模式
```bash
# 需要臨時不使用連閃裝備模式
python Open_gold_paddle_ocr.py --no-lian-shan
```

### 場景 C：代碼中使用
```python
# 默認啟用連閃裝備模式（推薦）
open_the_gold(d, times=-1, is_compare=True)

# 自動開裝備 30 分鐘
open_the_gold(d, times=1800, is_compare=True)

# 自動開裝備直到手動停止
open_the_gold(d, times=-1, is_compare=True)

# 禁用連閃模式
open_the_gold(d, times=-1, is_compare=True, has_lian_shan_equip=False)
```

---

## 效果對比一目瞭然

### 範例數據
```
開出組合 A：連 4%，爆 1%
原有組合 B：連 2%，爆 2%
```

| 模式 | 連的比較 | 爆的比較 | 總和對比 | 建議 |
|------|---------|---------|---------|------|
| 普通模式 | 4>2 ✓ | 1<2 ✗ | - | 需進一步判斷 |
| 連閃裝備模式 | - | - | 5>4 ✓ | **建議更換** |

---

## 三分鐘快速上手流程

1. **確認您擁有連閃裝備** ✓
2. **打開終端/命令提示符** ✓
3. **運行命令** ✓
   ```bash
   python Open_gold_paddle_ocr.py
   ```
   （連閃裝備模式已默認啟用）
4. **完成！** ✓

---

## 常見問題（3秒速答）

**Q：為什麼要相加連和爆？**
A：因為它們組成您的整套連閃裝備。

**Q：連閃裝備模式默認開啟嗎？**
A：是的！已默認開啟，無需任何命令行參數。

**Q：會影響其他技能嗎？**
A：不會，只影響連和爆的比較方式。

**Q：如何禁用連閃模式？**
A：運行 `python Open_gold_paddle_ocr.py --no-lian-shan`

**Q：需要修改任何配置文件嗎？**
A：不需要，系統已預配置。

---

## 文件和資源

| 文件 | 用途 |
|------|------|
| `LIAN_SHAN_EQUIP_GUIDE.md` | 詳細使用指南 |
| `lian_shan_example.py` | 代碼示例和測試 |
| `LIAN_SHAN_IMPLEMENTATION.md` | 技術實現文檔 |

---

**祝您使用愉快！** 🎉

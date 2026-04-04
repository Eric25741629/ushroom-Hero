# void 視為 empty 的修改總結

## 修改內容

### 1. COST_TABLE 更新
```python
COST_TABLE: Dict[str, Optional[int]] = {
    "empty": 0,
    "void": 0,  # 改為 0（原本是 None），視為 empty
    ...
}
```

### 2. enter_cost() 函數
```python
def enter_cost(lbl: str) -> Optional[int]:
    # unreachable_void 特殊處理：成本 = 0
    if lbl == "unreachable_void":
        return 0
    return COST_TABLE.get(base_label(lbl), None)
```

### 3. is_empty() 函數
```python
def is_empty(lbl: str) -> bool:
    base = base_label(lbl)
    # void 視為 empty
    return base == "empty" or base == "void"
```

## 效果

- **`void`** → 視為 `empty`（成本 0，可通行）
- **`unreachable_void`** → 視為 `unreachable_empty`（成本 0，可通行）
- Dijkstra 規劃時會優先選擇通過空洞而非挖掘岩石的路徑
- 你的例子中：到第 7 層 (6,2) 的 `unreachable_void` 成本從原本需要挖石頭變成直接成本 0

## 測試驗證

執行 `test_void_logic.py` 確認：
- ✅ void 和 unreachable_void 的 is_empty 都返回 True
- ✅ 成本都是 0
- ✅ Dijkstra 找到的路徑成本為 0（不需挖掘）

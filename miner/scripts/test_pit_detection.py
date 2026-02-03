"""測試 pit 檢測函數是否正確工作"""

def base_label(lbl: str) -> str:
    return lbl.replace("unreachable_", "")

# 舊版本（有 bug）
def is_unreachable_pit_old(lbl: str) -> bool:
    return base_label(lbl) == "unreachable_pit"

# 新版本（修正後）
def is_unreachable_pit_new(lbl: str) -> bool:
    return lbl == "unreachable_pit"

# 測試
test_cases = [
    "unreachable_pit",
    "reachable_pit",
    "dug_pit",
    "pit",
    "unreachable_dirt",
    "dirt",
]

print("測試 pit 檢測函數:")
print("="*60)
for label in test_cases:
    old_result = is_unreachable_pit_old(label)
    new_result = is_unreachable_pit_new(label)
    base = base_label(label)
    
    print(f"Label: {label:20} | base: {base:15} | 舊: {old_result:5} | 新: {new_result:5}")

print("\n問題說明:")
print("舊版本使用 base_label(lbl) 會移除 'unreachable_' 前綴")
print("所以 base_label('unreachable_pit') = 'pit'")
print("然後比較 'pit' == 'unreachable_pit' → False ❌")
print("\n新版本直接比較原始標籤")
print("所以 'unreachable_pit' == 'unreachable_pit' → True ✅")

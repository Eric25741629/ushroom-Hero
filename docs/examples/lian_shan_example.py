"""
連閃裝備使用示例
=================

本文件展示如何在代碼中直接使用連閃裝備功能。
"""

import uiautomator2 as u2
from Open_gold_paddle_ocr import open_the_gold, compare_skill_pairs

# ========================================
# 示例 1：命令行使用
# ========================================
# 在命令行運行以下命令啟用連閃裝備模式：
# python Open_gold_paddle_ocr.py --lian-shan


# ========================================
# 示例 2：在代碼中直接使用
# ========================================

def example_with_lian_shan_equip():
    """示例：使用連閃裝備模式"""
    
    # 連接設備
    device_ip = '7fe98fc6'
    d = u2.connect(device_ip)
    
    # 啟用連閃裝備模式
    # 參數說明：
    # - times: 執行時間（秒），-1 表示無限循環
    # - is_compare: 是否進行機率比對（True/False）
    # - has_lian_shan_equip: 是否擁有連閃裝備（True/False）
    open_the_gold(
        d, 
        times=-1, 
        is_compare=True, 
        has_lian_shan_equip=True  # 啟用連閃裝備模式
    )


def example_compare_with_lian_shan():
    """示例：直接使用比較函數"""
    
    # 模擬開出的詞條：連 3%，技能暴擊 2.5%
    rolled = [('連', 0.03), ('技', 0.025)]
    
    # 模擬原有的詞條：連 2%，技能暴擊 2%
    original = [('連', 0.02), ('技', 0.02)]
    
    # 不使用連閃裝備模式（連和爆分開比較）
    result_normal = compare_skill_pairs(
        rolled, 
        original, 
        is_compare=True,
        has_lian_shan_equip=False
    )
    print("普通模式結果：", result_normal['replace'], result_normal['reason'])
    
    # 使用連閃裝備模式（連和爆相加比較）
    result_lian_shan = compare_skill_pairs(
        rolled, 
        original, 
        is_compare=True,
        has_lian_shan_equip=True
    )
    print("連閃裝備模式結果：", result_lian_shan['replace'], result_lian_shan['reason'])


def example_scenario_1():
    """
    場景 1：普通開裝備
    
    情況：
    - 開出：連 3%，爆 2%（總和 5%）
    - 原有：連 2%，爆 1%（總和 3%）
    
    預期結果：
    - 普通模式：依序比較，連 3% > 2%，建議更換
    - 連閃裝備模式：(3%+2%) > (2%+1%)，即 5% > 3%，建議更換
    """
    rolled = [('連', 0.03), ('爆', 0.02)]
    original = [('連', 0.02), ('爆', 0.01)]
    
    result_normal = compare_skill_pairs(rolled, original, has_lian_shan_equip=False)
    result_lian_shan = compare_skill_pairs(rolled, original, has_lian_shan_equip=True)
    
    print("場景 1 - 開出更優的組合")
    print(f"  普通模式：{result_normal['replace']} ({result_normal['reason']})")
    print(f"  連閃裝備模式：{result_lian_shan['replace']} ({result_lian_shan['reason']})")
    print()


def example_scenario_2():
    """
    場景 2：開出連好，但爆較差
    
    情況：
    - 開出：連 4%，爆 1%（總和 5%）
    - 原有：連 2%，爆 2%（總和 4%）
    
    預期結果：
    - 普通模式：連 4% > 2%，建議更換
    - 連閃裝備模式：5% > 4%，建議更換（但相差更小）
    """
    rolled = [('連', 0.04), ('爆', 0.01)]
    original = [('連', 0.02), ('爆', 0.02)]
    
    result_normal = compare_skill_pairs(rolled, original, has_lian_shan_equip=False)
    result_lian_shan = compare_skill_pairs(rolled, original, has_lian_shan_equip=True)
    
    print("場景 2 - 連優爆差")
    print(f"  普通模式：{result_normal['replace']} ({result_normal['reason']})")
    print(f"  連閃裝備模式：{result_lian_shan['replace']} ({result_lian_shan['reason']})")
    print()


def example_scenario_3():
    """
    場景 3：只有一方有爆
    
    情況：
    - 開出：連 2%，爆 0%（總和 2%）
    - 原有：連 2%，爆 2%（總和 4%）
    
    預期結果：
    - 普通模式：連相同，爆 0% < 2%，不建議更換
    - 連閃裝備模式：2% < 4%，不建議更換
    """
    rolled = [('連', 0.02), ('爆', 0.0)]
    original = [('連', 0.02), ('爆', 0.02)]
    
    result_normal = compare_skill_pairs(rolled, original, has_lian_shan_equip=False)
    result_lian_shan = compare_skill_pairs(rolled, original, has_lian_shan_equip=True)
    
    print("場景 3 - 開出比較差")
    print(f"  普通模式：{result_normal['replace']} ({result_normal['reason']})")
    print(f"  連閃裝備模式：{result_lian_shan['replace']} ({result_lian_shan['reason']})")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("連閃裝備使用示例")
    print("=" * 60)
    print()
    
    # 執行示例場景
    example_scenario_1()
    example_scenario_2()
    example_scenario_3()
    
    print("=" * 60)
    print("場景分析完成")
    print("=" * 60)

# -*- coding: utf-8 -*-
"""
測試 opengold_v2 模組是否能正常匯入和基本運作
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, r'c:\nas同步_project\菇勇者全自動掛機')

try:
    # 測試基礎匯入
    from opengold_v2 import OpenGoldConfig
    print("[OK] OpenGoldConfig 匯入成功")
    
    from opengold_v2 import SkillEntry, Equipment, ComparisonResult
    print("[OK] Models 匯入成功")
    
    from opengold_v2 import OCRParser
    print("[OK] OCRParser 匯入成功")
    
    from opengold_v2 import SkillEvaluator
    print("[OK] SkillEvaluator 匯入成功")
    
    from opengold_v2 import ScreenshotLogger
    print("[OK] ScreenshotLogger 匯入成功")
    
    from opengold_v2 import DeviceDetector
    print("[OK] DeviceDetector 匯入成功")
    
    from opengold_v2 import UIController
    print("[OK] UIController 匯入成功")
    
    from opengold_v2 import LampService
    print("[OK] LampService 匯入成功")
    
    # 測試基本功能
    config = OpenGoldConfig()
    print(f"[OK] 配置建立成功: skip_incomplete_limit={config.skip_incomplete_limit}")
    
    # 測試資料模型
    entry1 = SkillEntry(code='技', probability=3.5)
    entry2 = SkillEntry(code='回', probability=0.3)
    equip = Equipment(entries=[entry1, entry2])
    print(f"[OK] Equipment 建立成功: combo={equip.get_combo()}")
    
    # 測試 OCR 解析
    parser = OCRParser(config)
    code = parser.text_to_skill_code("技能暴擊")
    print(f"[OK] OCR 解析測試: '技能暴擊' -> '{code}'")
    
    # 測試詞條比較
    rolled = Equipment.from_pairs([('技', 3.5), ('回', 0.3)])
    original = Equipment.from_pairs([('技', 3.0), ('回', 0.25)])
    evaluator = SkillEvaluator(config, has_lian_shan_equip=True)
    result = evaluator.compare_skill_pairs(rolled, original, is_compare=True)
    print(f"[OK] 詞條比較測試: should_replace={result.should_replace}, reason={result.reason}")
    
    # 測試連閃偵測
    detector = DeviceDetector(config)
    has_lian_shan = detector.detect_lian_shan_equip(['技回', '連閃', '反爆'])
    print(f"[OK] 連閃偵測測試: has_lian_shan={has_lian_shan}")
    
    print("\n[SUCCESS] 所有測試通過！opengold_v2 模組運作正常。")
    
except Exception as e:
    print(f"[FAIL] 測試失敗: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

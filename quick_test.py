# -*- coding: utf-8 -*-
"""
快速測試新架構的基本功能
"""
import logging
logging.basicConfig(level=logging.INFO)

def test_imports():
    """測試基本模組導入"""
    try:
        print("測試模組導入...")
        
        # 測試配置模組
        from config.game_config import config
        print("✓ 配置模組導入成功")
        
        # 測試核心模組
        from core.screenshot_manager import ScreenshotManager
        print("✓ 截圖管理器導入成功")
        
        from core.time_manager import TimeManager
        print("✓ 時間管理器導入成功")
        
        # 測試CNN模型
        from cnn_model import SimpleCNN
        cnn_model = SimpleCNN(num_classes=10)
        print("✓ CNN模型創建成功")
        
        # 測試主程式
        import new_main_modular
        print("✓ 主程式模組導入成功")
        
        print("\n🎉 所有基本模組導入測試通過！")
        return True
        
    except Exception as e:
        print(f"❌ 模組導入失敗: {e}")
        return False

def test_config():
    """測試配置系統"""
    try:
        print("\n測試配置系統...")
        from config.game_config import config
        
        # 檢查基本配置
        print(f"設備列表: {config.DEVICE_LIST}")
        print(f"截圖目錄: {config.SCREENSHOT_DIR}")
        print(f"檢測點配置: {len(config.DETECTION_POINTS)} 個")
        
        print("✓ 配置系統測試通過")
        return True
        
    except Exception as e:
        print(f"❌ 配置系統測試失敗: {e}")
        return False

def test_architecture():
    """測試架構完整性"""
    try:
        print("\n測試架構完整性...")
        
        # 檢查所有必需的文件是否存在
        import os
        required_files = [
            "config/game_config.py",
            "core/screenshot_manager.py",
            "core/state_detector.py",
            "core/device_manager.py",
            "core/task_scheduler.py",
            "core/time_manager.py",
            "core/game_controller.py",
            "core/game_actions.py",
            "new_main_modular.py"
        ]
        
        for file_path in required_files:
            if os.path.exists(file_path):
                print(f"✓ {file_path}")
            else:
                print(f"❌ 缺少文件: {file_path}")
                return False
        
        print("✓ 架構完整性檢查通過")
        return True
        
    except Exception as e:
        print(f"❌ 架構檢查失敗: {e}")
        return False

if __name__ == "__main__":
    print("🚀 開始新架構快速測試\n")
    
    tests = [
        test_imports,
        test_config,
        test_architecture
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print("-" * 50)
    
    print(f"\n📊 測試結果: {passed}/{total} 通過")
    
    if passed == total:
        print("🎉 新架構基本功能測試全部通過！")
        print("✅ 系統已準備好進行部署和進一步測試")
    else:
        print("⚠️  部分測試失敗，需要進一步檢查")

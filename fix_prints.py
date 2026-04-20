#!/usr/bin/env python3
"""
批量將 print 語句轉換為 logger 調用
"""
import re
import os
from pathlib import Path

# 需要處理的檔案列表 (排除測試和備份檔案)
FILES_TO_FIX = [
    "config_manager.py",
    "control_panel_app.py",
    "bot_state.py",
    "new_main_v2.py",
    "Open_gold_paddle_ocr.py",
    "ocr_server.py",
    "BUY.py",
    "daily_gift_task.py",
    "device_wrapper.py",
    "adb_operations.py",
    "control_panel_app.py",
]

# 需要添加 logger 導入的檔案 (還沒有 logger 的)
FILES_NEEDING_LOGGER_IMPORT = [
    "config_manager.py",
    "control_panel_app.py",
    "Open_gold_paddle_ocr.py",
    "ocr_server.py",
    "BUY.py",
    "daily_gift_task.py",
    "device_wrapper.py",
    "adb_operations.py",
]

def fix_print_statements(filepath: str) -> int:
    """將 print 語句轉換為 logger 調用，返回轉換數量"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"跳過不存在的檔案：{filepath}")
        return 0

    original_content = content
    count = 0

    # 模式 1: print(f"[XXX]...") 或 print(f"[XXX]...: {var}")
    # 替換為 logger.info/error/debug

    # [Config] 訊息
    content = re.sub(
        r'print\(f"\[Config\]([^:]*):\s*([^}]*)\{([^}]*)\}"\)',
        r'logger.info(f"[Config]\1: \2{\3}")',
        content
    )

    # 簡單訊息：print(f"[Config] xxx")
    content = re.sub(
        r'print\(f"\[Config\]([^:}]+)"\)',
        r'logger.info("[Config]\1")',
        content
    )

    # 計算變化
    lines_old = original_content.split('\n')
    lines_new = content.split('\n')

    for i, (old, new) in enumerate(zip(lines_old, lines_new), 1):
        if old != new and 'print' in old:
            count += 1

    # 寫回檔案
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✓ 已更新 {filepath}: {count} 處修改")

    return count

if __name__ == '__main__':
    for f in FILES_TO_FIX:
        if os.path.exists(f):
            fix_print_statements(f)
        else:
            print(f"⚠ 找不到檔案：{f}")

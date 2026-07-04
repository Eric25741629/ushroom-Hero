import time
import img_tools

# 方案 (loadout / 行裝) switch flow. Panel is a fixed-layout 540x960 modal:
#   - loadout dropdown header  ~(267,153)   (shows the active loadout name)
#   - apply button 「切換方案」 ~(271,705)   (only present when a *different* loadout
#                                            is selected; reads 「使用中」 otherwise)
#   - close X                  ~(271,890)
# Old flow clicked 「冒險行裝」+shift to reach the dropdown, but 冒險行裝 also appears as
# a bottom tab, so OCR hit the wrong instance and the dropdown never opened (everything
# downstream then timed out). We open the dropdown by its fixed position instead.
def switch_skill(d, skill_name='戰士推圖'):
    # 1. open 方案 panel from home
    if not img_tools.click_str_by_server(d, '方案', y_range=(699, 758), wait_timeout=3):
        return False
    time.sleep(1.5)
    # 2. expand the loadout dropdown (fixed header position)
    d.click(267, 153)
    time.sleep(1.5)
    # 3. pick the target loadout from the expanded list (exclude header/tabs via y_range).
    # ponytail: list assumed to fit (~10 items, last at y~607); add scroll if it overflows.
    if not img_tools.click_str_by_server(d, skill_name, y_range=(175, 665), wait_timeout=3):
        d.click(271, 890)            # not found -> close, do NOT apply a wrong loadout
        time.sleep(1.5)
        return False
    time.sleep(1.0)
    # 4. apply. 切換方案 only shows when selection != active; absent (使用中) = already applied.
    img_tools.click_str_by_server(d, '切換方案', wait_timeout=3)
    time.sleep(1.0)
    # 5. close panel back to home
    d.click(271, 890)
    time.sleep(1.5)
    return True

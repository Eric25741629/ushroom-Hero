import time
import img_tools

def switch_skill(d,skill_name='戰士推圖'):
    img_tools.click_str_by_server(d,'方案',y_range=(699,758))
    img_tools.click_str_by_server(d,'冒險行裝',shift_y=80)
    img_tools.click_str_by_server(d,skill_name)
    time.sleep(2)
    img_tools.click_str_by_server(d,'切換方案')
    d.click(275,870)
    time.sleep(2)

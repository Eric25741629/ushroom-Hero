"""
智慧型 UI 適配工具
支援 16:9, 18:9, 20:9 等多種解析度
"""

class UI_ANCHOR:
    CENTER = "center"           # 中心點縮放
    TOP_LEFT = "top_left"       # 錨定左上 (活動、頭像)
    BOTTOM_CENTER = "bottom"    # 錨定底部 (導覽列、領取按鈕)
    RATIO = "ratio"             # 全螢幕比例 (0.0~1.0)

class UI_COORDS:
    # --- 格式: (x, y, 錨定類型) ---
    
    # 主頁面底部導覽列 (適合 BOTTOM_CENTER)
    NAV_HOME = (321, 913, UI_ANCHOR.BOTTOM_CENTER) 
    NAV_BATTLE = (270, 920, UI_ANCHOR.BOTTOM_CENTER)
    
    # 家園內容按鈕 (適合 CENTER 縮放)
    FARM_CENTER = (452, 218, UI_ANCHOR.CENTER)
    TREASURE_BOX = (266, 369, UI_ANCHOR.CENTER)
    
    # 彈窗關閉按鈕 (通常相對於中心)
    CLOSE_POPUP = (274, 841, UI_ANCHOR.BOTTOM_CENTER)
    
    # 左上角區域 (適合 TOP_LEFT)
    AVATAR = (30, 30, UI_ANCHOR.TOP_LEFT)

def get_real_pos(coord_item, screen_w, screen_h):
    """
    動態計算適配後的座標
    base_res = (540, 960)
    """
    x, y, anchor = coord_item
    base_w, base_h = 540, 960
    
    if anchor == UI_ANCHOR.RATIO:
        return (int(x * screen_w), int(y * screen_h))
        
    if anchor == UI_ANCHOR.TOP_LEFT:
        # 直接按解析度縮放比例
        return (int(x * (screen_w / base_w)), int(y * (screen_h / base_h)))
        
    if anchor == UI_ANCHOR.BOTTOM_CENTER:
        # X 軸水平置中縮放，Y 軸從底部向上推
        # 假設在 16:9 中座標是 (x, 913)，代表離底部 47 像素
        offset_from_bottom = base_h - y
        real_x = x * (screen_w / base_w)
        real_y = screen_h - (offset_from_bottom * (screen_h / base_h))
        return (int(real_x), int(real_y))
        
    if anchor == UI_ANCHOR.CENTER:
        # 以螢幕中心為基準向外擴散
        # 適合處理遊戲中間的內容
        scale = min(screen_w / base_w, screen_h / base_h)
        center_x, center_y = screen_w / 2, screen_h / 2
        offset_x = (x - base_w / 2) * scale
        offset_y = (y - base_h / 2) * scale
        return (int(center_x + offset_x), int(center_y + offset_y))

    return (x, y) # Default

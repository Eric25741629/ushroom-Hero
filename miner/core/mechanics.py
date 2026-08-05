from typing import List, Tuple


def get_bomb_affected_cells_with_offscreen(
    r: int, c: int, rows: int, cols: int
) -> Tuple[List[Tuple[int, int]], int]:
    """回傳炸彈可見 footprint 與畫面下方的命中數。

    炸彈的幾何規則只在此處維護；第二個欄位保留 v3 executor 用來估算
    viewport 外收益的相容資訊。為維持既有 ``get_bomb_targets`` 數值契約，
    offscreen-bottom 只判斷列座標 ``nr >= rows``，不另外篩選欄座標。
    """
    raw = [(r + dr, c + dc) for dr in range(-1, 2) for dc in range(-1, 2)]
    raw.extend((r + dr, c + dc) for dr, dc in ((0, 2), (0, -2), (2, 0), (-2, 0)))
    visible = [(nr, nc) for nr, nc in raw if 0 <= nr < rows and 0 <= nc < cols]
    # 舊 API 即使列在畫面外、欄位同時越界，也會計入此相容計數；不要在
    # footprint 去重時順手改變這個數值語意。
    offscreen_bottom = sum(1 for nr, _nc in raw if nr >= rows)
    return visible, offscreen_bottom

def get_bomb_affected_cells(r: int, c: int, rows: int, cols: int) -> List[Tuple[int, int]]:
    """
    計算炸彈的影響範圍。
    規則：以 (r,c) 為中心的 3x3 區域，外加十字延伸 (距離2格的上下左右)。
    """
    return get_bomb_affected_cells_with_offscreen(r, c, rows, cols)[0]

def get_drill_affected_cells(r: int, c: int, rows: int, cols: int) -> List[Tuple[int, int]]:
    """
    計算鑽頭的影響範圍。
    規則：
    1. 當前行 (Column c) 從 r 到底部 (rows-1) 全部破壞。
    2. 底部 (rows-1) 的左右兩側 (c-1, c+1) 也破壞。
    """
    targets = []
    
    # 1. 垂直向下鑽到底
    for nr in range(r, rows):
        targets.append((nr, c))
        
    # 2. 底部左右擴散
    bottom_r = rows - 1
    for nc in [c - 1, c + 1]:
        if 0 <= nc < cols:
            targets.append((bottom_r, nc))
            
    return targets

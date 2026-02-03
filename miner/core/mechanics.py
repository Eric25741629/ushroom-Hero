from typing import List, Tuple

def get_bomb_affected_cells(r: int, c: int, rows: int, cols: int) -> List[Tuple[int, int]]:
    """
    計算炸彈的影響範圍。
    規則：以 (r,c) 為中心的 3x3 區域，外加十字延伸 (距離2格的上下左右)。
    """
    targets = []
    
    # 1. 3x3 區域
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            targets.append((r + dr, c + dc))
            
    # 2. 十字延伸 (距離 2)
    # 上下左右各延伸一格 (相對於 3x3 的邊界，即距離中心 2)
    for dr, dc in [(0, 2), (0, -2), (2, 0), (-2, 0)]:
        targets.append((r + dr, c + dc))
        
    # 過濾邊界
    valid_targets = []
    for nr, nc in targets:
        if 0 <= nr < rows and 0 <= nc < cols:
            valid_targets.append((nr, nc))
            
    return valid_targets

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

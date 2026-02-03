from typing import List, Tuple, Dict, Any
import pprint

# 為了能匯入同目錄下的 Mining（其內使用相對匯入），確保以套件方式載入
from miner.Mining import (
    plan_with_items_ev,
    plan_collect_all_mines_then_descend_v2,
    print_plan_result,
    DEFAULT_CLASSES,
)

H, W = 7, 6


def pretty_board(board: List[List[str]], col_width: int = 16) -> str:
    """以完整標籤、固定欄寬右對齊輸出，排版穩定不縮寫。
    例如 col_width=16 時，效果如：
      '           empty            empty            empty ...'
    """
    lines: List[str] = []
    for row in board:
        parts = [f"{cell:>{col_width}}" for cell in row]
        lines.append(" ".join(parts))
    return "\n".join(lines)


def empty_board(bottom_fill: str = "dirt") -> List[List[str]]:
    """產生空白盤面，但最底列一律非空地以避免觸發捲動。"""
    b = [["empty" for _ in range(W)] for _ in range(H)]
    for c in range(W):
        b[H-1][c] = bottom_fill
    return b


def fix_bottom_row(board: List[List[str]], fill: str = "dirt") -> None:
    """將第 7 列的 empty/dug_pit/unreachable_empty 修正為非空地。"""
    r = H - 1
    for c in range(W):
        if board[r][c] in ("empty", "dug_pit", "unreachable_empty"):
            board[r][c] = fill


def validate_no_bottom_empty(board: List[List[str]]) -> None:
    r = H - 1
    bad = [(r, c, board[r][c]) for c in range(W) if board[r][c] in ("empty", "dug_pit")]
    if bad:
        raise ValueError(f"底列不允許 empty/dug_pit，違規位置: {bad}")


def realistic_board(empty_ratio: float = 0.35) -> List[List[str]]:
    """產生較貼近實戰的盤面：空地比例較低，其餘分佈 dirt/rock/one_hit_rock + 少量 pit。
    - bottom row 強制使用障礙避免捲動。
    - pit 生成時若相鄰空地則標 reachable_pit，否則 unreachable_pit。
    """
    import random
    b = [["empty" for _ in range(W)] for _ in range(H)]
    # 先初始化非底列依機率填充
    for r in range(H - 1):
        for c in range(W):
            p = random.random()
            if p < empty_ratio:
                b[r][c] = "empty"
            else:
                # 障礙或礦坑再細分
                q = random.random()
                if q < 0.55:
                    b[r][c] = "dirt"
                elif q < 0.80:
                    b[r][c] = "rock"
                elif q < 0.92:
                    b[r][c] = "one_hit_rock"
                else:
                    b[r][c] = "pit"  # 先標 pit，稍後決定是否 reachable
    # 底列障礙（常見多 rock/dirt）
    for c in range(W):
        b[H-1][c] = random.choice(["rock", "dirt", "one_hit_rock"])

    # 將 pit 轉為 reachable/unreachable 版本
    def neighbors(r: int, c: int):
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            rr, cc = r+dr, c+dc
            if 0 <= rr < H and 0 <= cc < W:
                yield rr, cc
    for r in range(H - 1):
        for c in range(W):
            if b[r][c] == "pit":
                if any(b[rr][cc] in ("empty", "dug_pit") for rr, cc in neighbors(r, c)):
                    b[r][c] = "reachable_pit"
                else:
                    b[r][c] = "unreachable_pit"
    return b


def case_drill_vertical_column() -> List[List[str]]:
    # 讓鑽頭在某一欄自上而下省下>2鏟子（應觸發鑽頭）
    b = empty_board()
    c = 2  # 第3欄
    # 放一些成本較高的障礙，並混入一個礦坑
    b[1][c] = "rock"          # 2
    b[2][c] = "dirt"          # 1
    b[3][c] = "one_hit_rock"  # 1
    b[4][c] = "reachable_pit" # +1 (原本需敲一次)
    b[5][c] = "rock"          # 2
    b[6][c] = "dirt"          # 1
    # 最底列左右鄰加碼（鑽頭會多影響）
    if c-1 >= 0:
        b[6][c-1] = "rock"     # 2
    if c+1 < W:
        b[6][c+1] = "one_hit_rock"  # 1
    # 確保候選放置點周圍有空地
    b[0][c] = "empty"
    fix_bottom_row(b)
    return b


def case_bomb_dense_cluster() -> List[List[str]]:
    # 在一個空地中心周圍放高成本障礙，讓炸彈節省>3鏟子（應觸發炸彈）
    b = empty_board()
    r, c = 3, 3  # 中央作為放置點
    b[r][c] = "dug_pit"  # 可視為空地，允許放道具

    # 上下左右（2+2+2+2）
    b[r-1][c] = "rock"
    b[r+1][c] = "rock"
    b[r][c-1] = "rock"
    b[r][c+1] = "rock"

    # 斜對角（1+1+1+1）
    b[r-1][c-1] = "one_hit_rock"
    b[r-1][c+1] = "one_hit_rock"
    b[r+1][c-1] = "one_hit_rock"
    b[r+1][c+1] = "one_hit_rock"

    # 垂直±2（2+2）
    b[r-2][c] = "rock"
    b[r+2][c] = "rock"

    # 混入少量礦坑（每個+1鏟子等價）
    b[r-1][c-1] = "reachable_pit"
    b[r+1][c+1] = "reachable_pit"

    fix_bottom_row(b)
    return b


def case_low_ev_fallback() -> List[List[str]]:
    # 全盤幾乎都是空地，只有一個容易挖的礦坑（EV 不足道具門檻 → 回退 v2 規劃）
    b = empty_board()
    b[3][3] = "reachable_pit"  # 單一坑，只能省 1 鏟
    fix_bottom_row(b)
    return b


def case_both_good_pick_best() -> List[List[str]]:
    # 同時讓鑽頭與炸彈都有可觀收益，檢視誰的 EV 較高
    b = empty_board()
    # 炸彈候選區（中心 (3,2) 空地）
    b[3][2] = "empty"
    for rr, cc in [
        (2,2),(4,2),(3,1),(3,3),  # 上下左右 rock=2
        (2,1),(2,3),(4,1),(4,3),  # 斜角 one_hit_rock=1
        (1,2),(5,2)               # 垂直±2 rock=2
    ]:
        b[rr][cc] = "rock" if (rr,cc) in {(2,2),(4,2),(3,1),(3,3),(1,2),(5,2)} else "one_hit_rock"

    # 鑽頭候選欄（第5欄多障礙 + 礦坑）
    c = 4
    b[0][c] = "empty"
    b[1][c] = "rock"
    b[2][c] = "rock"
    b[3][c] = "reachable_pit"
    b[4][c] = "dirt"
    b[5][c] = "rock"
    b[6][c] = "one_hit_rock"
    if c-1 >= 0:
        b[6][c-1] = "rock"
    fix_bottom_row(b)
    return b

def case_cluster_3x3() -> List[List[str]]:
    # 建立一個 3x3 坑群，周圍以 rock/dirt 包圍，測試炸彈或鑽頭是否能大量省鏟
    b = empty_board(bottom_fill="rock")
    # 中心放在 (2,1)~(4,3)形成 3x3 (rows 2..4, cols 1..3)
    for r in range(2, 5):
        for c in range(1, 4):
            b[r][c] = "reachable_pit" if r != 2 else "unreachable_pit"  # 上邊界部分可能初始不可達
    # 周圍障礙加厚
    for c in range(1, 4):
        b[1][c] = "rock"
        b[5][c] = "rock"
    for r in range(2, 5):
        b[r][0] = "rock"
        b[r][4] = "rock"
    # 左上與右下再加一些 one_hit_rock
    b[1][0] = "one_hit_rock"
    b[5][4] = "one_hit_rock"
    fix_bottom_row(b)
    return b


def run_one(name: str, board: List[List[str]]):
    print("\n" + "="*80)
    print(f"[Case] {name}")
    # 先修補並驗證底列規則
    fix_bottom_row(board)
    validate_no_bottom_empty(board)
    print("Board (7x6):")
    print(pretty_board(board))

    items_available = {"drill": 1, "bomb": 1}
    plan = plan_with_items_ev(board, items_available, drill_threshold=2.0, bomb_threshold=3.0)
    print_plan_result("規劃結果", plan, board)

    if plan.get("mode") != "item_ev_plan":
        # 顯式展示回退 v2 的結果（雖然 plan 已是回退結果，但獨立展示更清楚）
        print("\n[Fallback v2] 額外展示 (如上非道具計畫)")
        plan_v2 = plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
        print_plan_result("規劃結果 v2", plan_v2, board)


def main():
    cases = [
        ("鑽頭 - 垂直省鏟(>2)", case_drill_vertical_column()),
        ("炸彈 - 高密度障礙(>3)", case_bomb_dense_cluster()),
        ("低 EV → 回退 v2", case_low_ev_fallback()),
        ("兩者皆佳，擇優", case_both_good_pick_best()),
        ("現實盤面(低空地密度)", realistic_board(empty_ratio=0.3)),
        ("現實盤面(中空地密度)", realistic_board(empty_ratio=0.45)),
        ("3x3 坑群測試", case_cluster_3x3()),
    ]
    for name, board in cases:
        run_one(name, board)


if __name__ == "__main__":
    main()


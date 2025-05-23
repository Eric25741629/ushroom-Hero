import cv2
import copy
import heapq
import torch
import torch.nn.functional as F
from torchvision import transforms
import numpy as np
from PIL import Image
from itertools import count
from typing import List, Tuple
# from miner.simplecnn import SimpleCNN
import os
import json
from datetime import datetime
from collections import defaultdict
import random
from typing import Union


class MiningPlanner:
    # ---- 常數（依實際遊戲可再調）----
    H, W = 7, 6
    DIS_TO_BASE = {f'dis_{t}': t for t in [
    'gold', 'silver', 'copper', 'skill', 'partner', 'drill', 'boom']}
                             # 棋盤格數  (y, x)
    tr = transforms.Compose([
        transforms.Resize((64, 64)),
        transforms.ToTensor(),
    ])
    TREASURE_BASE = {'gold', 'silver', 'copper', 'skill', 'partner','drill','boom'}
    TREASURES = TREASURE_BASE | {f'dis_{t}' for t in TREASURE_BASE}

    HARDNESS = {'dirt': 1, 'rock': 2, 'one-step-rock': 1,
                'copper': 1, 'silver': 1, 'gold': 1,
                'skill': 1, 'partner': 1}

    SCORE = {'copper': 300, 'silver': 700, 'gold': 3000,
             'skill': 500, 'partner': 500,'drill':750,'boom':750,}
    top_left = (6, 227)
    bottom_right = (535, 852)
    # ---- 新增：讓 dis_* 也有殘值（這一行就夠）----
    SCORE.update({f'dis_{t}': 50 for t in TREASURE_BASE})
    class_names = ["boom",
                   "copper",
                   "dirt",
                   "dis_boom",
                   "dis_copper",
                   "dis_dirt",
                   "dis_drill",
                   "dis_e",
                   "dis_gold",
                   "dis_partner",
                   "dis_rock",
                   "dis_rube",
                   "dis_silver",
                   "dis_skill",
                   "drill",
                   "e",
                   "gold",
                   "one-step-rock",
                   "partner",
                   "rock",
                   "rube",
                   "silver",
                   "skill",]
    DIR4 = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    uid = count()                  # for heap tie-break
    # ----------------- 估價函式 -----------------
    def cost_of(self, cell): return self.HARDNESS.get(cell, 1)
    def score_of(self, cell): return self.SCORE.get(cell, 0)

    # ---- 初始化 ----
    def __init__(self,
                 model: torch.nn.Module,
                 board_lt: Tuple[int, int] = (6, 227),        # 影像左上格 (x,y)
                 board_rb: Tuple[int, int] = (535, 852),        # 影像右下格 (x,y)
                 device='cpu'):
        self.model = model.to(device)
        self.model.eval()
        self.lt = board_lt
        self.rb = board_rb
        self.device = device
        self.default_max_path_len = 7
        self.default_path_len_penalty = 0.2
        # 預算 / beam 參數可外調
        self.cell_w = int(round((self.rb[0] - self.lt[0]) / self.W))
        self.cell_h = int(round((self.rb[1] - self.lt[1]) / self.H))
    # ---- 工具函式 ----
    @staticmethod
    def in_bounds(
        y, x): return 0 <= y < MiningPlanner.H and 0 <= x < MiningPlanner.W
    # -------- 訓練資料記錄 --------

    def save_training_example(self, board, actions, value, output_file="train_data.jsonl", image_path=None):
        example = {
            "timestamp": datetime.now().isoformat(),
            "board": board,
            "actions": actions,
            "value": value,
        }
        if image_path is not None and isinstance(image_path, (str, np.str_)):
            example["image_path"] = image_path
            example["board_id"] = os.path.basename(image_path)
        with open(output_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")
    # -------- 1. 影像 → 7×6 類別矩陣 --------
    # def classify_board(self, img_path: str) -> List[List[str]]:
    # 圖片格式可能是路徑或陣列

    def classify_board(self, img: Union[str, np.ndarray]) -> List[List[str]]:
        if isinstance(img, str):
            img = cv2.imread(img)
        x0, y0 = self.lt
        x1, y1 = self.rb
        cell_w = int(round((x1-x0)/MiningPlanner.W))
        cell_h = int(round((y1-y0)/MiningPlanner.H))

        board = []
        for r in range(MiningPlanner.H):
            row = []
            for c in range(MiningPlanner.W):
                cx0 = x0 + c*cell_w
                cy0 = y0 + r*cell_h
                cell = img[cy0:cy0+cell_h, cx0:cx0+cell_w]
                cell_pil = Image.fromarray(
                    cv2.cvtColor(cell, cv2.COLOR_BGR2RGB))
                tensor = self.tr(cell_pil).unsqueeze(0).to(self.device)
                with torch.no_grad():
                    out = self.model(tensor)
                    prob = F.softmax(out, 1)
                    conf, pred = torch.max(prob, 1)
                label = "unknown" if conf.item(
                ) < 0.75 else self.class_names[pred.item()]
                row.append(label)
            board.append(row)
        return board

    def find_paths_with_free_walk(self, grid, max_dig=4, max_path_len=10, path_len_penalty_factor=0.01): # 新增參數並給予預設值
        # 優先挖掘目標：裸露的寶藏
        direct_dig_targets = []
        for y in range(self.H):
            for x in range(self.W):
                cell = grid[y][x]
                if cell in self.TREASURE_BASE:  # 僅限基本寶藏，不含 dis_*
                    # 檢查是否裸露（周圍是否有 'e'）
                    is_exposed = any(
                        self.in_bounds(y + dy, x + dx) and grid[y + dy][x + dx] == 'e'
                        for dx, dy in self.DIR4
                    )
                    if is_exposed:
                        direct_dig_targets.append((x, y))

        # 如果有裸露的寶藏，則直接挖掘它們
        if direct_dig_targets:
            moves = [{
                'type': 'dig',
                'origin': p,
                'cost': self.cost_of(grid[p[1]][p[0]]),  # 單步花多少
                'score': self.score_of(grid[p[1]][p[0]]),
                'repeat': self.cost_of(grid[p[1]][p[0]])  # 要敲幾下
            } for p in direct_dig_targets
            ]
            # 假設成本是所有挖掘目標的總成本
            best_val = sum(self.cost_of(grid[p[1]][p[0]]) for p in direct_dig_targets)
            return moves, best_val

        best_val, best_moves = float('inf'), None

        # 1️⃣ 所有 e 空格都能當起點
        start_cells = [(x, y) for y in range(self.H)
                    for x in range(self.W) if grid[y][x] == 'e']

        # ------------------------------------------------------------------
        for sx, sy in start_cells:
            # path, cost, score, digs, tset
            # 初始路徑只有起點，長度為 1
            stack = [([(sx, sy)], 0, 0, 0, set())]

            while stack:
                path, cost, score, digs, tset = stack.pop()

                # 注意：成本 (cost) 在這裡已經包含了之前步驟累積的長度懲罰
                net = cost - score
                if net < best_val:
                    best_val, best_moves = net, path

                # 如果目前路徑長度已達上限，則不再從此路徑擴展
                # path 的長度代表已經過的格子數
                if len(path) >= max_path_len:
                    continue

                cx, cy = path[-1]
                for dx, dy in self.DIR4:
                    nx, ny = cx + dx, cy + dy
                    if not self.in_bounds(ny, nx) or (nx, ny) in path:
                        continue

                    cell = grid[ny][nx]

                    # 計算這一步的長度懲罰
                    # 即使是移動到 'e'，也計算懲罰，鼓勵更短的總路徑
                    current_step_penalty = path_len_penalty_factor

                    if cell == 'e':
                        # 新的成本 = 原有成本 + 這一步的長度懲罰
                        stack.append((path + [(nx, ny)], cost + current_step_penalty, score, digs, tset))
                    else: # 是可挖掘物
                        if digs >= max_dig:
                            continue

                        # 新的成本 = 原有成本 + 挖掘成本 + 這一步的長度懲罰
                        ncost  = cost + self.cost_of(cell) + current_step_penalty
                        nscore = score
                        ntset  = tset.copy()
                        if cell in self.TREASURES and (nx, ny) not in ntset:
                            nscore += self.score_of(cell)
                            ntset.add((nx, ny))
                        stack.append((path + [(nx, ny)],
                                    ncost, nscore, digs + 1, ntset))
        # ------------------------------------------------------------------
        # ⬇️ 這行開始才是「全部 DFS 結束後」的收尾 ========================
        digs_only = [p for p in (best_moves or []) if grid[p[1]][p[0]] != 'e']

        # ───── 新規則：如果還沒有任何可挖步驟 ──────
        if not digs_only:
            bottom = self.H - 1                       # 最底列 index

            # ❶ 最底列已有 e ⇒ 這回合不用挖
            if any(grid[bottom][x] == 'e' for x in range(self.W)):
                return [], 0

            # ❷ 從最底列挑 cost 最低的可挖格
            min_cost = float('inf')
            fallback = None
            for x in range(self.W):
                cell = grid[bottom][x]
                # 由於 ❶ 已處理 bottom row 有 'e' 的情況，
                # 此處 if cell == 'e' 條件實際上不會為 True，主要目的是跳過 'dis_*'。
                if cell == 'e' or cell.startswith('dis_'):
                    continue
                c = self.cost_of(cell)
                if c < min_cost:
                    min_cost, fallback = c, (x, bottom)

            # ❸ 如果最底列全是 'dis_*' (且根據 ❶，無 'e')，則往上搜尋替代方案
            if fallback is None:
                # 從倒數第二列 (H-2) 往上搜尋到第 0 列
                found_alternative_fallback = False
                for r_idx in range(self.H - 2, -1, -1): # 從 H-2 迭代至 0
                    current_row_min_cost = float('inf')
                    current_row_fallback_candidate = None
                    for c_idx in range(self.W):
                        cell_above = grid[r_idx][c_idx]
                        # 我們要找可以挖掘的格子，所以跳過 'e' 和 'dis_*'
                        if cell_above == 'e' or cell_above.startswith('dis_'):
                            continue

                        cost_above = self.cost_of(cell_above)
                        if cost_above < current_row_min_cost:
                            current_row_min_cost = cost_above
                            current_row_fallback_candidate = (c_idx, r_idx)

                    if current_row_fallback_candidate:
                        # 在 r_idx 列找到了可挖掘的格子，將其設為 fallback
                        # 並更新 min_cost 為這個格子的成本
                        min_cost = current_row_min_cost
                        fallback = current_row_fallback_candidate
                        found_alternative_fallback = True
                        break # 已找到最優先的替代列 (由下往上第一列包含可挖掘物)，停止搜尋

                if not found_alternative_fallback:
                    # 即使往上搜尋，也沒找到任何可挖掘的格子
                    # (代表整個板面除了 'e' 就是 'dis_*'，且最底層是 'dis_*')
                    return [], float('inf')

            # 若 fallback 有值 (無論是來自最底列或上方列)，則設定挖掘動作
            # min_cost 此時已是 fallback 目標的實際成本
            digs_only = [fallback]
            best_val  = min_cost

        moves = [{
                'type'  : 'dig',
                'origin': p,
                'cost'  : self.cost_of(grid[p[1]][p[0]]),   # 單步花多少
                'score' : self.score_of(grid[p[1]][p[0]]),
                'repeat': self.cost_of(grid[p[1]][p[0]])    # 要敲幾下
            } for p in digs_only
            ]

        return moves, best_val



    # -------- 4. 對外主函式 --------
    def board_to_image_coords(self, x: int, y: int, offset=(0, 0)) -> Tuple[int, int]:
        img_x = self.lt[0] + x * self.cell_w + offset[0]
        img_y = self.lt[1] + y * self.cell_h + offset[1]
        return img_x, img_y
    def plan(self, img_path: str, save_to: str = None,image_path=None) -> Tuple[List[List[str]], List[dict], float]:
        board = self.classify_board(img_path)
        moves, val = self.find_paths_with_free_walk(board, max_dig=4)
        if save_to:
            self.save_training_example(
                board, moves, val, output_file=save_to, image_path=img_path,)
        return board, moves, val

resize_size = (64, 64)
import torch
import torch.nn as nn
import torch.nn.functional as F

class SimpleCNN(nn.Module):
    def __init__(self, num_classes):
        super(SimpleCNN, self).__init__()
        self.conv1 = nn.Conv2d(3, 16, 3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(16, 32, 3, padding=1)
        self.fc1 = nn.Linear(32 * (resize_size[0]//4) * (resize_size[1]//4), 128)
        self.fc2 = nn.Linear(128, num_classes)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))  # -> 16x64x64
        x = self.pool(F.relu(self.conv2(x)))  # -> 32x32x32
        x = x.view(x.size(0), -1)             # 展平成向量
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x


# ======== 圖像前處理 ========

if __name__ == "__main__":
    import uiautomator2 as u2
    import time
    import random

    simpleCNN = SimpleCNN(num_classes=23)
    # 假設你已經有 trained_model, img_transform, class_names
    trained_model = simpleCNN
    trained_model.load_state_dict(torch.load(
        r"A:\菇勇者全自動掛機\miner\oralce_model.pth"))
    trained_model.eval()
    planner = MiningPlanner(trained_model,
                            device='cuda')
    # d= u2.connect('emulator-5554')
    # img = d.screenshot(format='opencv')
    img=cv2.imread(r"A:/recording\emulator-5554_1746801815144233.jpg")
    board, moves, value = planner.plan(img)
    print("分類矩陣:")
    for r in board:
        print(r)
    last_dy = 0                      # 0 = 沒捲動；-1 = 向上位移 1 列
    prev_xy = None                   # 紀錄前一步

    for idx, move in enumerate(moves):        # idx = 0,1,2…
        x, y = move["origin"]
        times  = move["repeat"]
        # 如上一輪挖在最底列且本輪不是同一格，代表畫面已經下捲
        if prev_xy is not None and prev_xy[1] == 6 and times != 2:
            last_dy -= 1             # 所有 y 座標往上平移一格

        # 轉成螢幕座標
        img_x, img_y = planner.board_to_image_coords(
            x, y + last_dy, offset=(44, 44))

        print(f"Dig {idx+1} at board ({x},{y}) → pixel ({img_x},{img_y})")
        for _ in range(times):
            print(img_x, img_y)
        time.sleep(0.5)

        prev_xy = (x, y)
    # if moves:
    #     # print("\n推薦行動序列:")
    #     # for i, a in enumerate(moves, 1):
    #     #     print(
    #     #         f"Step{i}: {a['type']}→{a['origin']}  cost={a['cost']} gain={a['score']}")
    #     # print("淨效用:", value)
    #     img=cv2.imread("A:/main3_1746086766.9355874.jpg")

    #     for move in moves:
    #         x, y = move["origin"]
    #         if y
    #         for i in range(move["repeat"]):
    #             img_x, img_y = planner.board_to_image_coords(x, y, offset=(44, 44))
    #             print(f"Dig at board ({x},{y}) → image pixel ({img_x},{img_y})")
    #             cv2.circle(img, (img_x, img_y), 20, (0, 255, 255), -1)
    #             cv2.imshow("Digging Path", img)
    #             cv2.waitKey(0)




    else:
        print("無 3 步內路徑，建議挖底層捲動")

# =========================
# 遊戲規則 (Game Rules / 調整後最新說明)
# =========================
# 1. 盤面尺寸：
#    - 固定 7 列 × 6 欄，自上而下列索引 0..6（顯示可視為第 1~7 層）。
#
# 2. 目標：
#    - 以最少鏟子資源挖掘(開採)更多礦洞獲得獎勵，並推進盤面捲動以揭露新層。
#
# 3. 資源與道具：
#    - 鏟子：挖掘障礙類格（如泥土 dirt、石頭 rock、one_hit_rock）。
#    - 鑽頭 (Drill)：放置在空地 (empty 或 dug_pit) 上，效果為「自放置點向下的垂直直線」直到最底列，且在最底列額外影響其左右相鄰各一格（若存在）。
#    - 炸彈 (Bomb)：放置在空地 (empty 或 dug_pit) 上，影響範圍為 5x5 菱形/十字：中心 (0,0)、上下左右 (±1,0),(0,±1)、四個近對角 (±1,±1)、以及額外 (±2,0)。
#
# 4. 連通與可達：
#    - 使用 4 向連通（上下左右）判定是否「可達」。
#    - 遊戲上的礦狀態只有兩種：pit（未挖開）與 dug_pit（已挖開）。
#    - 標籤 reachable_pit 與 pit 同義（皆為「未挖開的礦坑」），是否可達由 4 向連通決定，非獨立狀態。
#    - 當與空地 4 向相鄰時，玩家可對該 pit 進行挖掘；挖開後格子變為 dug_pit。
#
# 5. 礦洞 (pit)：
#    - 未挖開的礦坑統稱 pit（模型可能以 reachable_pit 呈現，語意相同）。
#    - pit 被鏟子敲擊一次（或被道具影響）後，立即變為 dug_pit 並獲得獎勵。
#
# 6. 道具與礦洞交互：
#    - 道具本身不「破壞」礦洞；若道具範圍覆蓋到尚未挖的礦洞，視為直接將該礦洞挖開並轉成 dug_pit（等同完成採集）。
#
# 7. 空地定義：
#    - empty 與 dug_pit 均視為可放置道具的空地。
#    - 規則與程式不再使用 void 名詞；若模型仍輸出 void，視同 empty 處理。
#
# 8. 盤面推進（上捲）：
#    - 當最底列 (row=6) 任一格由障礙/礦洞變成空地 (empty 或 dug_pit) 時（不論手動挖掘或道具造成），盤面立即整體向上捲動一層：原第 1 列消失，其他列往上移，新增一列作為新的最底列。
#
# 9. 多格礦洞：
#    - 遊戲可能存在 1x1 / 2x2 / 3x3 礦坑；目前邏輯僅將其視為獨立單格處理，尚未進行多格聚合與整體獎勵加成判定。
#
# 10. 成本與獎勵（概要）：
#    - 障礙類格（dirt/rock/one_hit_rock）需要鏟子；empty / dug_pit 不需鏟子。
#    - 礦洞挖開時獲得固定獎勵（實際值見 REWARD_TABLE）。
#    - 道具不消耗鏟子，但會帶來路徑清除與直接開採的綜合效益。
#
# （以下為道具/規劃相關程式碼示意，可能與最新行為不同時須同步調整）
 

import os
from typing import Any, Dict, List, Tuple, Union, Optional, Sequence
import cv2
import numpy as np
from PIL import Image
import io
import requests
from datetime import datetime
from math import inf
import time
import sys
import base64
import uiautomator2 as u2
import random
# PyTorch imports for CNN
import torch
from torchvision import transforms

# 導入 SimpleCNN
try:
    from .simplecnn import SimpleCNN, resize_size
    from .rl_recorder import RLRecorder

except ImportError:
    from simplecnn import SimpleCNN, resize_size
    from rl_recorder import RLRecorder
from img_tools import analyze_skill_via_http
# =========================
# 基本設定
# =========================
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_CNN_MODEL = os.path.join(os.path.dirname(__file__), "checkpoints", "best.pth")
expected_points = [
    ((382,  79), (17, 20, 28)),
    ((307, 151), (19, 25, 48)),
    ((397, 180), (20, 16, 22)),
    ((315,  72), (21, 20, 24)),
    ((350, 117), (22, 24, 42)),
    ((429, 138), (33, 36, 64)),
    ((289, 189), (14, 21, 48)),
    ((132,  56), (11, 18, 38)),
    ((500, 216), (35, 42, 51)),
]
MIN_REQUIRED = 5   # 至少要符合的點數
TOL = 3        # 容忍度：逐通道允許 ±TOL（依需要調整）

# ----- 可調的成本與獎勵 -----
# None = 不可進入；其餘為「進入該格要付的鏟子成本」
COST_TABLE: Dict[str, Optional[int]] = {
    "empty": 0,
    "dirt": 1,
    "rock": 2,
    "one_hit_rock": 1,  # 你的新類型
    # 礦洞需要敲擊一次才會變成 dug_pit
    "pit": 1,
}
REWARD_TABLE: Dict[str, int] = {
    "pit": 50,  # 自行依實測調整;越大越會優先採礦
}
MINE_LABELS = set(REWARD_TABLE.keys())

# ====== 重新定義：成本/礦標籤 ======
COST_TABLE.update({
    "dug_pit": 0,              # 已開採 → 視為 empty
    "reachable_pit": 1,        # 與空地相鄰但尚未挖開 → 需要 1 成本敲擊
    "unreachable_pit": None,   # 未連通前不可通行
    "unreachable_empty": None, # 未連通的空地不可通行，需鬆弛後才變 empty
})

REWARD_TABLE.update({
    "reachable_pit": REWARD_TABLE.get("pit", 12),
    "unreachable_pit": REWARD_TABLE.get("pit", 12),
    "dug_pit": 0,
})

DEFAULT_CLASSES = [
    "dirt",
    "dug_pit",
    "empty",
    "one_hit_rock",
    "reachable_pit",
    "rock",
    "unreachable_dirt",
    "unreachable_pit",
    "unreachable_rock",
    "unreachable_empty",
]


def _resolve_device(device: Optional[Union[str, torch.device]]) -> torch.device:
    if device is None:
        return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    return torch.device(device) if isinstance(device, str) else device


def load_cnn_model(
    model_path: Optional[str] = None,
    device: Optional[Union[str, torch.device]] = None,
) -> Tuple[SimpleCNN, List[str], torch.device]:
    resolved_device = _resolve_device(device)
    path = model_path or DEFAULT_CNN_MODEL
    if not os.path.exists(path):
        raise FileNotFoundError(f"Model file not found: {path}")

    checkpoint = torch.load(path, map_location=resolved_device)
    classes = list(checkpoint.get('classes', DEFAULT_CLASSES)) or list(DEFAULT_CLASSES)
    model = SimpleCNN(num_classes=len(classes))
    model.load_state_dict(checkpoint['model_state'])
    model.to(resolved_device)
    model.eval()
    setattr(model, 'classes', classes)

    print(f"[CNN Loader] Loaded model from {path}")
    print(f"[CNN Loader] Device: {resolved_device}")
    print(f"[CNN Loader] Classes: {classes}")

    return model, classes, resolved_device

# =========================
# 影像分類 - CNN
# =========================
class ClassifierCNN:
    """CNN-based classifier using a pre-loaded SimpleCNN model."""

    def __init__(
        self,
        model: SimpleCNN,
        classes: Optional[Sequence[str]] = None,
        device: Optional[Union[str, torch.device]] = None,
        dataset_root: Optional[str] = None,
    ):
        if model is None:
            raise ValueError("ClassifierCNN requires a pre-loaded model instance. Use load_cnn_model first.")

        self.dataset_root = dataset_root

        if device is None:
            try:
                resolved_device = next(model.parameters()).device
            except StopIteration:
                resolved_device = _resolve_device(None)
        else:
            resolved_device = _resolve_device(device)

        self.device = resolved_device
        self.model = model.to(self.device)
        self.model.eval()

        if classes:
            self.classes = list(classes)
        else:
            model_classes = getattr(model, 'classes', None)
            self.classes = list(model_classes) if model_classes else list(DEFAULT_CLASSES)


        if not self.classes:
            raise ValueError("ClassifierCNN requires class labels; provide via parameter or set model.classes.")
        self.skipped_samples: Dict[str, int] = {}
        #填入預設類別
        for cls in self.classes:
            self.skipped_samples[cls] = 0
        self.transform = transforms.Compose([
            transforms.Resize(resize_size),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])

        print(f"[CNN Classifier] Device: {self.device}")
        print(f"[CNN Classifier] Classes: {self.classes}")

    def _normalize_label(self, label: str) -> str:
        """將舊版/非預期標籤正規化為目前支援集合。
        - unreachable_void -> unreachable_empty
        - void -> empty
        其他維持不變。
        """
        if label == "unreachable_void":
            return "unreachable_empty"
        if label == "void":
            return "empty"
        return label

    def classify_board(self, img: Union[str, np.ndarray, Image.Image], save_samples: bool = True, save_conf_threshold: float = 0.8) -> Tuple[List[List[str]], List[List[float]]]:
        # Accept file path, OpenCV numpy array (BGR) or PIL Image
        if isinstance(img, str):
            img = cv2.imread(img)
        elif isinstance(img, Image.Image):
            # PIL Image -> numpy RGB -> convert to BGR for OpenCV slicing/IO
            arr = np.array(img)
            if arr.ndim == 2:
                arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            else:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            img = arr

        if img is None:
            raise ValueError('image not found or unreadable')
        
        H, W = 7, 6
        # 依你截圖座標切格子（如 UI 有變動，需同步更新）
        x0, y0 = 6, 227
        x1, y1 = 535, 852
        cell_w = int(round((x1 - x0) / W))
        cell_h = int(round((y1 - y0) / H))
        
        board: List[List[str]] = []
        confidences: List[List[float]] = []
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        with torch.no_grad():
            for r in range(H):
                row: List[str] = []
                conf_row: List[float] = []
                for c in range(W):
                    cx0 = x0 + c * cell_w
                    cy0 = y0 + r * cell_h
                    cell = img[cy0:cy0 + cell_h, cx0:cx0 + cell_w]
                    
                    # Convert BGR to RGB for PIL
                    cell_rgb = cv2.cvtColor(cell, cv2.COLOR_BGR2RGB)
                    cell_pil = Image.fromarray(cell_rgb)
                    
                    # Transform and predict
                    cell_tensor = self.transform(cell_pil).unsqueeze(0).to(self.device)
                    output = self.model(cell_tensor)
                    probs = torch.nn.functional.softmax(output, dim=1)
                    conf, pred_idx = torch.max(probs, dim=1)
                    
                    raw_label = self.classes[pred_idx.item()]
                    label = self._normalize_label(raw_label)
                    confidence = conf.item()
                    
                    row.append(label)
                    conf_row.append(confidence)
                    
                    # Save low confidence samples
                    # if save_samples and self.dataset_root and confidence < save_conf_threshold:
                    #     label_dir = os.path.join(self.dataset_root)
                    #     os.makedirs(label_dir, exist_ok=True)
                    #     fname = f"{timestamp}_r{r}_c{c}_{label}_conf{confidence:.4f}.png"
                    #     cv2.imwrite(os.path.join(label_dir, fname), cell)
                    #save high confidence samples
                    if save_samples and self.dataset_root and confidence >= 0.95 and confidence <= 0.99 and self.skipped_samples.get(label, 0) == 0:
                        label_dir = os.path.join(self.dataset_root, label)
                        os.makedirs(label_dir, exist_ok=True)
                        #若該資料夾超過1000張，則不再儲存，且紀錄下來 下次不在重新檢查
                        if len(os.listdir(label_dir)) >= 1000:
                            self.skipped_samples[label] = self.skipped_samples.get(label, 0) + 1
                            continue
                        fname = f"{timestamp}_r{r}_c{c}_{label}_conf{confidence:.4f}.png"
                        cv2.imwrite(os.path.join(label_dir, fname), cell)
                board.append(row)
                confidences.append(conf_row)
        
        # print board
        # print("[Board - CNN]")
        # for r in board:
        #     print(' | '.join f"{x:^20}" for x in r))
        return board, confidences
def to_bgr_np(image):
    """
    將 image（可能是 PIL.Image、ndarray(BGR/RGB)、或 bytes）統一轉成 OpenCV 用的 BGR np.ndarray
    """
    # 1) 如果是 PIL.Image
    if isinstance(image, Image.Image):
        arr = np.array(image)  # 這是 RGB 的 ndarray
        if arr.ndim == 3 and arr.shape[2] == 3:
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        elif arr.ndim == 3 and arr.shape[2] == 4:  # RGBA
            return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        else:
            raise ValueError(f"不支援的 PIL 影像 shape: {arr.shape}")

    # 2) 如果是 numpy.ndarray
    if isinstance(image, np.ndarray):
        arr = image
        # 嘗試判斷通道順序（無法 100% 保證，但一般規律足夠）
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if arr.ndim == 3 and arr.shape[2] == 3:
            # 假設已是 BGR（OpenCV 慣例）；若其實是 RGB，可視情況再轉
            return arr
        if arr.ndim == 3 and arr.shape[2] == 4:
            # 假設 BGRA
            return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        raise ValueError(f"不支援的 ndarray 影像 shape: {arr.shape}")

    # 3) 如果是 bytes（少見，但保險）
    if isinstance(image, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(image)).convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    raise TypeError(f"未知的影像型別：{type(image)}")

    

# =========================
# 規劃工具：採礦/下樓
# =========================
def base_label(lbl: str) -> str:
    return lbl.replace("unreachable_", "")

def enter_cost(lbl: str) -> Optional[int]:
    """進入該格要付的鏟子成本；None = 不能進（牆/不可達）。"""
    # 設定：對於被標為 unreachable_ 的格子，若其原始材質是可挖的障礙（dirt/rock/one_hit_rock/pit），
    # 我們應該允許規劃器把它當成可以挖的格子，成本為對應的 COST_TABLE 值。
    # 但對於 unreachable_empty（本質為空地但暫不可達）仍視為不可直接進入，需透過鬆弛或先挖鄰近格子來曝光。
    if lbl.startswith("unreachable_"):
        b = base_label(lbl)
        # unreachable_empty 保持 None（不可進入）
        if b == "empty":
            return None
        # 其他原本有成本的類型，回傳對應成本，讓規劃可以選擇從這些格子挖通
        return COST_TABLE.get(b, None)
    return COST_TABLE.get(base_label(lbl), None)

def is_empty(lbl: str) -> bool:
    # empty 和 dug_pit 視為空地（可直接作為起點）
    return lbl in ("empty", "dug_pit")

 

def is_mine(lbl: str) -> bool:
    return base_label(lbl) in MINE_LABELS

def is_pit(lbl: str) -> bool:
    b = base_label(lbl)
    return b in {"pit", "reachable_pit", "unreachable_pit", "dug_pit"}  # 相容舊字

def is_dug_pit(lbl: str) -> bool:
    return lbl == "dug_pit"

def is_reachable_pit(lbl: str) -> bool:
    return lbl == "reachable_pit"

def is_unreachable_pit(lbl: str) -> bool:
    return lbl == "unreachable_pit"

def list_all_pits(board: List[List[str]]) -> Tuple[List[Tuple[int,int]], List[Tuple[int,int]], List[Tuple[int,int]]]:
    """回傳 (dug, reachable, unreachable) 三組座標"""
    R, C = len(board), len(board[0])
    dug, rea, unrea = [], [], []
    for r in range(R):
        for c in range(C):
            if is_dug_pit(board[r][c]):
                dug.append((r,c))
            elif is_reachable_pit(board[r][c]):
                rea.append((r,c))
            elif is_unreachable_pit(board[r][c]):
                unrea.append((r,c))
    return dug, rea, unrea

from .path_planner_utils import (
    dijkstra_from_all_empties as _dijkstra_from_all_empties,
    reconstruct_path as _reconstruct_path,
    summarize_path as _summarize_path,
    mark_path_as_empty as _mark_path_as_empty,
    floor7_triggered as _floor7_triggered,
)

def dijkstra_from_all_empties(board: List[List[str]]):
    return _dijkstra_from_all_empties(board, is_empty, enter_cost)

def reconstruct_path(prev: List[List[Optional[Tuple[int,int]]]], end: Tuple[int,int]):
    return _reconstruct_path(prev, end)

def summarize_path(board: List[List[str]], path: List[Tuple[int,int]]):
    return _summarize_path(board, path, is_empty, enter_cost)

def mark_path_as_empty(board: List[List[str]], dig_list: List[Tuple[int,int]]):
    return _mark_path_as_empty(board, dig_list)

def floor7_triggered(board: List[List[str]]):
    return _floor7_triggered(board, is_empty)

# （已移除舊規劃：min_cost_floor7 / greedy_with_rewards，以專注使用 v2 規劃）

# =========================
# 主程式：截圖→分類→規劃
# =========================
def print_plan_result(title: str, result: Dict[str, Any], orig_board: List[List[str]]):
    print(f"\n[{title}]")
    print(result.get("message", ""))
    if not result.get("ok", False):
        return
    steps = result.get("steps")
    if steps:  # 貪婪版本
        for i, s in enumerate(steps, 1):
            print(f"  Step {i}: {s['action']} -> {s['target']}  cost={s['step_cost']}  gain={s.get('gain')}")
            for (r,c) in s["dig_list"]:
                lbl = orig_board[r][c]
                print(f"    dig ({r},{c}) : {lbl} -> cost {enter_cost(lbl)}")
    else:  # 最短達樓版本
        dig_list = result.get("dig_list", [])
        if dig_list:
            print("需要挖的座標（row,col）與原始類別與成本：")
            for (r,c) in dig_list:
                lbl = orig_board[r][c]
                print(f"  ({r},{c}) : {lbl} -> cost {enter_cost(lbl)}")
# ====== 把規劃步驟轉成實際點擊 ======
# 依你原本的切格範圍 (x0,y0,x1,y1) 與 H=7, W=6 做座標換算
GRID_CFG = {
    "H": 7, "W": 6,
    "x0": 6, "y0": 227,
    "x1": 535, "y1": 852,
}

# 每種地形需要點擊幾下
HIT_TABLE = {
    "empty": 0,
    "dirt": 1,
    "rock": 2,
    "one_hit_rock": 1,
    "pit": 1,             # 礦洞需敲擊一次
    "reachable_pit": 1,   # 與空地相鄰但未挖開 → 需敲擊
    "dug_pit": 0,         # 已開採完成不需要再點
}

def cell_center_xy(r: int, c: int) -> Tuple[int, int]:
    H, W = GRID_CFG["H"], GRID_CFG["W"]
    x0, y0, x1, y1 = GRID_CFG["x0"], GRID_CFG["y0"], GRID_CFG["x1"], GRID_CFG["y1"]
    cell_w = int(round((x1 - x0) / W))
    cell_h = int(round((y1 - y0) / H))
    cx = x0 + c * cell_w + cell_w // 2
    cy = y0 + r * cell_h + cell_h // 2
    return cx, cy

def material_of(label: str) -> str:
    return label.replace("unreachable_", "")

def required_hits(label: str) -> int:
    # 優先檢查完整標籤 (例如 unreachable_empty)
    if label in HIT_TABLE:
        return HIT_TABLE[label]
    return HIT_TABLE.get(material_of(label), 0)

def tap_cell(d, r: int, c: int, hits: int, wait_ms: int = 150) -> None:
    """在 (r,c) 這格點擊 hits 次。"""
    x, y = cell_center_xy(r, c)
    for _ in range(hits):
        d.click(x+random.randint(-10,10), y+random.randint(-10,10))
        d.sleep(wait_ms / 1000.0)
    time.sleep(0.5 + random.random() * 0.5)  # 點擊後稍微等一下

def verify_cell_empty(d, clf: ClassifierCNN, r: int, c: int, max_retry: int = 3, error_threshold: float = 0.9) -> bool:
    """
    重新截圖+分格，確認 (r,c) 是否已變 empty；最多重試 max_retry 次。
    如果信心度 < error_threshold (0.95)，視為可能有UI擋住，點擊空白處關閉UI後重新判斷。
    """
    for retry in range(max_retry):
        passed, n_ok = check_points(d.screenshot(format="opencv"), expected_points, tol=TOL, min_required=MIN_REQUIRED)
        if passed:
            d.click(444+random.randint(-10,10),107+random.randint(-10,10))  # 點擊畫面中間偏上位置，關閉可能的干擾UI
            continue
        img2 = d.screenshot()
        board2, confidences2 = clf.classify_board(img2, save_samples=False)
        confidence = confidences2[r][c]
        
        # 檢查信心度，如果 < 0.95 可能有UI擋住視野
        if confidence < error_threshold:
            print(f"    ⚠️ 信心度過低 ({r},{c}): {confidence:.4f} < {error_threshold}, 點擊空白處關閉UI")
            d.click(517, 44)  # 點擊空白處關閉可能的UI元素
            # 點擊後重新判斷，下次就不會因為同樣原因再觸發
            continue
        
        # 檢查是否已變成 empty
        if base_label(board2[r][c]) == "empty":
            return True
    return False

def execute_plan_steps(
    d,
    clf: ClassifierCNN,
    board: List[List[str]],
    steps: List[Dict[str, Any]],
    rl_recorder: Optional[RLRecorder] = None,
) -> None:
    """
    依規劃步驟逐格執行：
      - mine/descend 都是一串 dig_list 需要依序打通
      - 每格依材質點擊 N 次，結束後做一次驗證
      - 特別注意：如果挖到第七層(r=6)，會立即觸發下樓，必須停止當前步驟
      - 新增：處理 use_drill / use_bomb 道具使用
    """
    try:
        for i, s in enumerate(steps, 1):
            print(f"\n[執行 Step {i}] {s['action']} -> {s['target']}  預期成本={s['step_cost']}  預期收益={s.get('gain')}")

            # ---- 處理道具使用 ----
            if s["action"].startswith("use_"):
                item_type = s["action"].split("_", 1)[1]
                r, c = s["target"]
                print(f"  - 於 ({r},{c}) 使用 {item_type}")
                tap_cell(d, r, c, 1, wait_ms=500)  # 點擊一次放置道具
                # 道具使用後，整個盤面改變，剩下的步驟無效，應立即停止執行並重新規劃
                print("    ⚠️ 使用道具後盤面已改變，停止執行，將重新規劃")
                time.sleep(2.5)  # 等待動畫
                return  # 中斷執行，強制外部迴圈重新規劃

            step_board_before = [row[:] for row in board]
            cell_events: List[Dict[str, Any]] = []
            for (r, c) in s["dig_list"]:
                lbl = board[r][c]
                mat = material_of(lbl)
                hits = required_hits(lbl)
                print(f"  - 挖 ({r},{c}) {lbl} → 需要點擊 {hits} 次")
                cell_event: Dict[str, Any] = {
                    "row": r,
                    "col": c,
                    "label_before": lbl,
                    "material": mat,
                    "required_hits": hits,
                    "enter_cost": enter_cost(lbl),
                }
                if hits > 0:
                    tap_cell(d, r, c, hits, wait_ms=1000)
                    passed, n_ok = check_points(d.screenshot(format="opencv"), expected_points, tol=TOL, min_required=MIN_REQUIRED)

                    # 如果是 pit，等待收集動畫結束

                # 驗證；若失敗，可再補點一次（保守些）
                # 但如果是第七層(r=6) 不需要驗證 (因為下樓後會自動進入下一層)
                if r < 6:  # 只有前 6 列需要驗證
                    ok = verify_cell_empty(d, clf, r, c, max_retry=2)
                    success = ok
                    if not ok:
                        # 補點一次（或依 mat 再點一次）
                        print(f"    驗證未成功，補點一次 ({r},{c})")
                        tap_cell(d, r, c, 1)
                        success = verify_cell_empty(d, clf, r, c, max_retry=1)
                    cell_event["verify_success"] = success
                else:
                    print(f"    第七層格子 ({r},{c}) 跳過驗證")
                    print(f"    ⚠️ 觸發下樓，停止執行剩餘路徑，請重新規劃")
                    cell_event["verify_success"] = True
                    cell_events.append(cell_event)
                    if rl_recorder:
                        rl_event = {
                            "step_index": i,
                            "plan_action": s["action"],
                            "target": s["target"],
                            "step_cost_expected": s.get("step_cost"),
                            "gain_expected": s.get("gain"),
                            "cell_events": cell_events,
                            "board_before": step_board_before,
                            "board_after": None,
                            "terminated": "floor7",
                        }
                        rl_recorder.record_transition(rl_event)
                    # 挖到第七層會立即下樓，後續的格子座標會失效，必須中斷
                    return
                cell_events.append(cell_event)
            # 每個 step 結束後，更新 board（供下一步列印材質使用，不影響實際判定）
            img2 = d.screenshot()
            board, _ = clf.classify_board(img2, save_samples=False)
            if rl_recorder:
                rl_event = {
                    "step_index": i,
                    "plan_action": s["action"],
                    "target": s["target"],
                    "step_cost_expected": s.get("step_cost"),
                    "gain_expected": s.get("gain"),
                    "cell_events": cell_events,
                    "board_before": step_board_before,
                    "board_after": [row[:] for row in board],
                }
                rl_recorder.record_transition(rl_event)
    except Exception as e:
        #印出錯誤行數
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        print(f"[執行錯誤] {fname} 第 {exc_tb.tb_lineno} 行: {e}")

# =========================
# 道具期望值規劃（EV-based item planning）
# =========================
from typing import Set

def get_drill_affected_cells(r: int, c: int, H: int, W: int) -> List[Tuple[int, int]]:
    affected: List[Tuple[int,int]] = []
    for i in range(r, H):
        affected.append((i, c))
    if c > 0:
        affected.append((H - 1, c - 1))
    if c < W - 1:
        affected.append((H - 1, c + 1))
    seen: Set[Tuple[int,int]] = set()
    out: List[Tuple[int,int]] = []
    for p in affected:
        if 0 <= p[0] < H and 0 <= p[1] < W and p not in seen:
            seen.add(p); out.append(p)
    return out

def get_bomb_affected_cells(r: int, c: int, H: int, W: int) -> List[Tuple[int, int]]:
    rel = [
        (0,0), (-2,0), (2,0),
        (-1,-1), (-1,0), (-1,1),
        (0,-1), (0,1),
        (1,-1), (1,0), (1,1),
    ]
    cells: List[Tuple[int,int]] = []
    for dr, dc in rel:
        rr, cc = r+dr, c+dc
        if 0 <= rr < H and 0 <= cc < W:
            cells.append((rr,cc))
    return cells

def _is_pit_like(lbl: str) -> bool:
    b = base_label(lbl)
    return b in {"pit", "reachable_pit", "unreachable_pit"}

def _neighbors4(r: int, c: int, R: int, C: int):
    for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
        rr, cc = r+dr, c+dc
        if 0 <= rr < R and 0 <= cc < C:
            yield rr, cc

def _pit_positions_for_ev(board: List[List[str]]) -> List[Tuple[int,int]]:
    R, C = len(board), len(board[0])
    out: List[Tuple[int,int]] = []
    for r in range(R):
        for c in range(C):
            b = base_label(board[r][c])
            if b in {"pit", "reachable_pit", "unreachable_pit"}:
                out.append((r,c))
    return out

def _min_cost_to_mine_specific_pit(board: List[List[str]], dist: List[List[float]], pos: Tuple[int,int]) -> float:
    r, c = pos
    R, C = len(board), len(board[0])
    if base_label(board[r][c]) == "dug_pit":
        return 0.0
    best = float('inf')
    for rr, cc in _neighbors4(r, c, R, C):
        d = dist[rr][cc]
        if d < best:
            best = d
    if best == float('inf'):
        return float('inf')
    return best + 1.0  # 站在相鄰格 + 敲擊一次

def _min_cost_to_mine_any_pit(board: List[List[str]], dist: List[List[float]]) -> Tuple[float, Optional[Tuple[int,int]]]:
    pits = _pit_positions_for_ev(board)
    best_cost = float('inf')
    best_pos: Optional[Tuple[int,int]] = None
    for pos in pits:
        cost = _min_cost_to_mine_specific_pit(board, dist, pos)
        if cost < best_cost:
            best_cost = cost
            best_pos = pos
    return best_cost, best_pos

def _cost_to_descend(board: List[List[str]], dist: List[List[float]]) -> float:
    R, C = len(board), len(board[0])
    last = R - 1
    best = float('inf')
    for c in range(C):
        if dist[last][c] < best:
            best = dist[last][c]
    return best

def _apply_item_effect(board: List[List[str]], affected: List[Tuple[int,int]]) -> List[List[str]]:
    bd = [row[:] for row in board]
    for (rr,cc) in affected:
        lbl = bd[rr][cc]
        if _is_pit_like(lbl):
            bd[rr][cc] = "dug_pit"
        else:
            b = base_label(lbl)
            if b in ("dirt","rock","one_hit_rock"):
                # 清除障礙。保持可達性語意：原本 unreachable_* → 先標 unreachable_empty，再交由鬆弛傳播。
                bd[rr][cc] = "unreachable_empty" if lbl.startswith("unreachable_") else "empty"
    return bd

def _can_place_item(lbl: str) -> bool:
    # 僅允許放在 empty / dug_pit；不直接放在 pit 上
    return lbl in {"empty", "dug_pit"}

def _find_pit_squares(board: List[List[str]], size: int) -> List[Set[Tuple[int,int]]]:
    R, C = len(board), len(board[0])
    out: List[Set[Tuple[int,int]]] = []
    for r in range(R - size + 1):
        for c in range(C - size + 1):
            cells: Set[Tuple[int,int]] = set()
            ok = True
            for i in range(size):
                for j in range(size):
                    rr, cc = r+i, c+j
                    if not _is_pit_like(board[rr][cc]):
                        ok = False; break
                    cells.add((rr,cc))
                if not ok: break
            if ok:
                out.append(cells)
    return out

def _relax_unreachable_empty_inplace(bd: List[List[str]]) -> None:
    R, C = len(bd), len(bd[0])
    def has_empty_neighbor(r: int, c: int) -> bool:
        return any(is_empty(bd[rr][cc]) for rr, cc in _neighbors4(r, c, R, C))
    changed = True
    while changed:
        changed = False
        for r in range(R):
            for c in range(C):
                if bd[r][c] == "unreachable_empty" and has_empty_neighbor(r, c):
                    bd[r][c] = "empty"
                    changed = True

def _total_cost_to_collect_pits(board: List[List[str]], pit_set: Set[Tuple[int,int]]) -> float:
    # 近似：每次選取目前最便宜的坑，打通路徑+敲一次，更新盤面後重算
    bd = [row[:] for row in board]
    remaining = set(pit_set)
    total = 0.0
    while remaining:
        dist, prev = dijkstra_from_all_empties(bd)
        best_cost = float('inf')
        best_pit = None
        best_path: List[Tuple[int,int]] = []
        for (r,c) in list(remaining):
            if base_label(bd[r][c]) == 'dug_pit':
                remaining.discard((r,c));
                continue
            R, C = len(bd), len(bd[0])
            min_d = float('inf')
            near = None
            for rr, cc in _neighbors4(r, c, R, C):
                if dist[rr][cc] < min_d:
                    min_d = dist[rr][cc]
                    near = (rr,cc)
            if near is None or min_d == float('inf'):
                continue
            path = reconstruct_path(prev, near)
            dig_list, step_cost = summarize_path(bd, path)
            cost_here = step_cost + 1.0
            if cost_here < best_cost:
                best_cost = cost_here
                best_pit = (r,c)
                best_path = dig_list
        if best_pit is None or best_cost == float('inf'):
            return float('inf')
        mark_path_as_empty(bd, best_path)
        bd[best_pit[0]][best_pit[1]] = 'dug_pit'
        _relax_unreachable_empty_inplace(bd)
        total += best_cost
        remaining.discard(best_pit)
    return total

def find_best_item_placement_ev_route(
    board: List[List[str]],
    item_type: str,
    dist0: List[List[float]],
) -> Dict[str, Any]:
    R, C = len(board), len(board[0])
    get_aff = get_drill_affected_cells if item_type == "drill" else get_bomb_affected_cells
    base_pit_cost, base_pit_target = _min_cost_to_mine_any_pit(board, dist0)
    base_desc_cost = _cost_to_descend(board, dist0)
    # 基線：先挖最佳單個坑，再下樓 的合併成本
    def _min_combined_cost(board_now: List[List[str]], dist_now: List[List[float]]) -> float:
        pits = _pit_positions_for_ev(board_now)
        if not pits:
            return _cost_to_descend(board_now, dist_now)
        best = float('inf')
        R, C = len(board_now), len(board_now[0])
        for pos in pits:
            r, c = pos
            if base_label(board_now[r][c]) == 'dug_pit':
                # 已挖 → 只算下樓
                best = min(best, _cost_to_descend(board_now, dist_now))
                continue
            cost_mine = _min_cost_to_mine_specific_pit(board_now, dist_now, pos)
            if cost_mine == float('inf'):
                continue
            bd_tmp = [row[:] for row in board_now]
            bd_tmp[r][c] = 'dug_pit'
            _relax_unreachable_empty_inplace(bd_tmp)
            dist_tmp, _ = dijkstra_from_all_empties(bd_tmp)
            cost_desc = _cost_to_descend(bd_tmp, dist_tmp)
            if cost_desc == float('inf'):
                continue
            best = min(best, cost_mine + cost_desc)
        return best

    base_combined_cost = _min_combined_cost(board, dist0)
    # 預先找出坑群（2x2、3x3）
    clusters_2 = _find_pit_squares(board, 2)
    clusters_3 = _find_pit_squares(board, 3)

    best = {"pos": None, "savings": -float('inf'), "objective": None, "target": None, "affected": [],
            "base_pit_cost": base_pit_cost, "base_desc_cost": base_desc_cost,
            "item_pit_cost": None, "item_desc_cost": None,
            "base_combined_cost": base_combined_cost, "item_combined_cost": None,
            "pre_cost": 0.0, "pre_dig_list": []}

    # 需要 prev 來重建路徑
    dist_start, prev_start = dijkstra_from_all_empties(board)

    for r in range(R):
        for c in range(C):
            # 方案A：直接可放（空地）
            cand_variants = []
            if _can_place_item(board[r][c]):
                cand_variants.append({
                    "pre_cost": 0.0,
                    "pre_dig_list": [],
                    "prep_board": [row[:] for row in board],
                })

            # 方案B：先挖成空地再放（僅考慮可達，且不在坑上挖）
            # [Fix] 暫時停用「為了放道具而特地去挖路」的邏輯，避免：
            # 1. 瘋狂挖空地 (Digging empty ground to place item)
            # 2. 陷入死路或無限迴圈 (Prepare step fails -> Retry -> Loop)
            # 3. 收益計算過於樂觀 (Net savings small but risk high)
            # if not _can_place_item(board[r][c]) and not _is_pit_like(board[r][c]):
            #     if dist_start[r][c] < float('inf'):
            #         path_to_cell = reconstruct_path(prev_start, (r, c))
            #         pre_dig_list, pre_cost = summarize_path(board, path_to_cell)
            #         bd_prep = [row[:] for row in board]
            #         mark_path_as_empty(bd_prep, pre_dig_list)
            #         _relax_unreachable_empty_inplace(bd_prep)
            #         cand_variants.append({
            #             "pre_cost": float(pre_cost),
            #             "pre_dig_list": pre_dig_list,
            #             "prep_board": bd_prep,
            #         })

            if not cand_variants:
                continue

            for variant in cand_variants:
                pre_cost = variant["pre_cost"]
                bd_base = variant["prep_board"]
                affected = get_aff(r, c, R, C)
                bd2 = _apply_item_effect(bd_base, affected)
                _relax_unreachable_empty_inplace(bd2)
                # 防護：若經過 (prep) 處理後，目標格仍非可放置（例如仍為 unreachable_empty），
                # 則跳過此 variant。只有當 prep_board（包括前置挖掘）能把目標變為
                # 實際的 'empty' 或 'dug_pit' 時，才允許放置道具。
                if bd2[r][c] not in {"empty", "dug_pit"}:
                    # 例外：若該 variant 有前置挖掘（pre_cost>0），但仍未變為可放置，代表前置路徑
                    # 並未處理到目標，仍然不安全，跳過。
                    continue
                dist2, _ = dijkstra_from_all_empties(bd2)
                item_pit_cost, item_pit_target = _min_cost_to_mine_any_pit(bd2, dist2)
                item_desc_cost = _cost_to_descend(bd2, dist2)
                item_combined_cost = _min_combined_cost(bd2, dist2)
                save_pit = (base_pit_cost - item_pit_cost) if base_pit_cost < float('inf') else -float('inf')
                save_desc = (base_desc_cost - item_desc_cost) if base_desc_cost < float('inf') else -float('inf')
                save_combined = (base_combined_cost - item_combined_cost) if base_combined_cost < float('inf') else -float('inf')

                # 針對坑群評估（近似）：
                save_cluster = -float('inf')
                cluster_label = None
                # 直接用「受影響坑集合」作為一個候選群，處理 1x2、1x3 等非正方形情況
                affected_pits = {(rr,cc) for (rr,cc) in affected if _is_pit_like(board[rr][cc])}
                if affected_pits:
                    base_cost = _total_cost_to_collect_pits(board, set(affected_pits))
                    item_cost = _total_cost_to_collect_pits(bd2, set(affected_pits))
                    if base_cost < float('inf') and item_cost < float('inf'):
                        sv = base_cost - item_cost
                        if sv > save_cluster:
                            save_cluster = sv
                            cluster_label = f"mine_cluster_affected({len(affected_pits)})"
                    # 2x2 群
                    for cells in clusters_2:
                        base_cost = _total_cost_to_collect_pits(board, set(cells))
                        item_cost = _total_cost_to_collect_pits(bd2, set(cells))
                        if base_cost < float('inf') and item_cost < float('inf'):
                            sv = base_cost - item_cost
                            if sv > save_cluster:
                                save_cluster = sv
                                cluster_label = 'mine_cluster_2x2'
                    # 3x3 群
                    for cells in clusters_3:
                        base_cost = _total_cost_to_collect_pits(board, set(cells))
                        item_cost = _total_cost_to_collect_pits(bd2, set(cells))
                        if base_cost < float('inf') and item_cost < float('inf'):
                            sv = base_cost - item_cost
                            if sv > save_cluster:
                                save_cluster = sv
                                cluster_label = 'mine_cluster_3x3'

                    # 先取單目標較大者
                    if save_pit >= save_desc:
                        savings = save_pit
                        objective = "mine_pit"
                        target = item_pit_target if item_pit_target is not None else base_pit_target
                    else:
                        savings = save_desc
                        objective = "descend"
                        target = None

                    # 若坑群省鏟更高，替換為坑群目標
                    if save_cluster > savings:
                        savings = save_cluster
                        objective = cluster_label
                        target = None
                    # 若合併目標(挖坑+下樓)更高，使用合併
                    if save_combined > savings:
                        savings = save_combined
                        objective = "mine_then_descend"
                        target = None

                    # 扣掉前置挖掘成本
                    net_savings = float(savings) - float(pre_cost)
                    if net_savings > best["savings"]:
                        best = {
                        "pos": (r,c),
                        "savings": float(net_savings),
                        "objective": objective,
                        "target": target,
                        "affected": affected,
                        "base_pit_cost": float(base_pit_cost),
                        "base_desc_cost": float(base_desc_cost),
                        "base_combined_cost": float(base_combined_cost),
                        "item_pit_cost": float(item_pit_cost),
                        "item_desc_cost": float(item_desc_cost),
                        "item_combined_cost": float(item_combined_cost),
                        "pre_cost": float(pre_cost),
                        "pre_dig_list": list(variant.get("pre_dig_list", [])),
                    }
    return best

def plan_with_items_ev(
    board: List[List[str]],
    items_available: Dict[str,int],
    drill_threshold: float = 2.0,
    bomb_threshold: float = 3.0,
) -> Dict[str, Any]:
    """
    Only consider using items (drill/bomb) when the minimal shovel-based cost to
    either mine a pit or descend is greater than 2.0. Otherwise prefer pure
    shovel planning.
    """
    dist0, _ = dijkstra_from_all_empties(board)

    # Evaluate baseline shovel costs
    try:
        base_pit_cost, _ = _min_cost_to_mine_any_pit(board, dist0)
    except Exception:
        base_pit_cost = float('inf')
    try:
        base_desc_cost = _cost_to_descend(board, dist0)
    except Exception:
        base_desc_cost = float('inf')

    base_best = min(base_pit_cost, base_desc_cost)
    # If shovel-only solution is cheap (<=2), skip item planning entirely
    if base_best <= 2.0:
        print(f"[Item EV] Skipping item evaluation because base shovel cost {base_best:.1f} <= 2.0")
        # Fallback to baseline planning without items
        try:
            return plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
        except Exception:
            return {
                "ok": False,
                "mode": "item_ev_plan",
                "message": "Skipped item planning; fallback baseline failed.",
                "steps": [],
                "total_cost": 0,
                "total_reward": 0,
                "board_after": [row[:] for row in board],
            }

    best_candidates: List[Dict[str, Any]] = []
    best_drill: Optional[Dict[str, Any]] = None
    best_bomb: Optional[Dict[str, Any]] = None
    if items_available.get("drill", 0) > 0:
        d = find_best_item_placement_ev_route(board, "drill", dist0)
        best_drill = d
        if d.get("pos") and d.get("savings", -inf) >= drill_threshold:
            best_candidates.append({**d, "item": "drill"})
    if items_available.get("bomb", 0) > 0:
        b = find_best_item_placement_ev_route(board, "bomb", dist0)
        best_bomb = b
        if b.get("pos") and b.get("savings", -inf) >= bomb_threshold:
            best_candidates.append({**b, "item": "bomb"})
    candidate = max(best_candidates, key=lambda x: x["savings"]) if best_candidates else None
    if candidate:
        bd = [row[:] for row in board]
        for (rr,cc) in candidate.get("affected", []):
            lbl = bd[rr][cc]
            if _is_pit_like(lbl):
                bd[rr][cc] = "dug_pit"
            elif enter_cost(lbl) is not None:
                bd[rr][cc] = "empty"
        steps: List[Dict[str, Any]] = []
        pre_cost = float(candidate.get("pre_cost", 0.0))
        pre_list = candidate.get("pre_dig_list", [])
        if pre_cost > 0 and pre_list:
            steps.append({
                "action": "prepare_item_spot",
                "target": candidate["pos"],
                "step_cost": pre_cost,
                "gain": None,
                "path": list(pre_list),
                "dig_list": list(pre_list),
            })
        steps.append({
            "action": f"use_{candidate['item']}",
            "target": candidate["pos"],
            "step_cost": 0,
            "gain": float(candidate["savings"]),
            "path": [candidate["pos"]],
            "dig_list": [],
            "affected_cells": candidate.get("affected", []),
        })
        if candidate.get('objective') == 'mine_pit':
            msg_obj = '挖礦'
        elif candidate.get('objective') == 'descend':
            msg_obj = '下樓'
        elif candidate.get('objective') == 'mine_then_descend':
            msg_obj = '挖礦+下樓'
        else:
            msg_obj = str(candidate.get('objective'))
        # 額外資訊：同時顯示坑/下樓各自的節省，並提示實際優先順序
        pit_sav = None
        desc_sav = None
        comb_sav = None
        try:
            bpc = candidate.get('base_pit_cost', float('inf'))
            ipc = candidate.get('item_pit_cost', float('inf'))
            bdc = candidate.get('base_desc_cost', float('inf'))
            idc = candidate.get('item_desc_cost', float('inf'))
            bcc = candidate.get('base_combined_cost', float('inf'))
            icc = candidate.get('item_combined_cost', float('inf'))
            pit_sav = (bpc - ipc) if bpc < float('inf') and ipc < float('inf') else None
            desc_sav = (bdc - idc) if bdc < float('inf') and idc < float('inf') else None
            comb_sav = (bcc - icc) if bcc < float('inf') and icc < float('inf') else None
        except Exception:
            pass
        has_reachable = any(is_reachable_pit(board[r][c]) for r in range(len(board)) for c in range(len(board[0])))
        extra = []
        if pit_sav is not None or desc_sav is not None:
            extra.append(f"pit節省={pit_sav:.1f}" if pit_sav is not None else "pit節省=NA")
            extra.append(f"desc節省={desc_sav:.1f}" if desc_sav is not None else "desc節省=NA")
        if comb_sav is not None:
            extra.append(f"合併節省={comb_sav:.1f}")
        if pre_cost > 0:
            extra.append(f"前置挖掘成本={pre_cost:.1f}")
        if has_reachable:
            extra.append("盤面存在 reachable_pit，實際執行仍會先挖礦再下樓")
        extra_msg = ("；" + "，".join(extra)) if extra else ""
        return {
            "ok": True,
            "mode": "item_ev_plan",
            "message": (
                f"使用 {candidate['item']} 於 {candidate['pos']}，節省鏟子={candidate['savings']:.1f}，目標={msg_obj}{extra_msg}"
            ),
            "steps": steps,
            "total_cost": 0,
            "total_reward": 0,
            "board_after": bd,
        }
    # fallback
    # Debug：若沒有入選的道具，輸出最佳候選資訊以利分析
    try:
        dbg_drill = best_drill or {}
        dbg_bomb = best_bomb or {}
        print(
            f"[Item EV Debug] no item chosen. drill: pos={dbg_drill.get('pos')}, savings={dbg_drill.get('savings')}, obj={dbg_drill.get('objective')} | "
            f"bomb: pos={dbg_bomb.get('pos')}, savings={dbg_bomb.get('savings')}, obj={dbg_bomb.get('objective')}"
        )
    except Exception:
        pass
    try:
        plan_collect_all_mines_v4_cost_agnostic(board, descend_after_collect=True)
    except Exception:
        return plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
import random

def plan_collect_all_mines_v4_cost_agnostic(
    board: List[List[str]],
    descend_after_collect: bool = True,
) -> Dict[str, Any]:
    """
    V4 強迫症規劃：
    1. 不計成本：只要場上還有礦，就必須去挖。
    2. 絕對優先：先採集貼著空地的礦。
    3. 隨機路徑：若有多個礦坑的挖掘成本相同，隨機選擇一個目標，避免死板。
    4. 只有在全場無礦時，才會下樓。
    """
    bd = [row[:] for row in board]
    R, C = len(bd), len(bd[0])
    steps: List[Dict[str, Any]] = []
    total_cost = 0
    total_reward = 0

    # --- 輔助工具 ---
    def neighbors(r: int, c: int):
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            rr, cc = r+dr, c+dc
            if 0 <= rr < R and 0 <= cc < C:
                yield rr, cc

    def has_empty_neighbor(r: int, c: int) -> bool:
        return any(is_empty(bd[rr][cc]) for rr, cc in neighbors(r, c))

    def relax_unreachable_empty_as_empty() -> int:
        changed = 0
        again = True
        while again:
            again = False
            for r in range(R):
                for c in range(C):
                    if bd[r][c] == "unreachable_empty" and has_empty_neighbor(r, c):
                        bd[r][c] = "empty"
                        changed += 1
                        again = True
        return changed

    def collect_immediate_reachable() -> int:
        """收割所有現在手指點得到的礦 (成本=1)"""
        count = 0
        # 必須反覆掃描，因為挖掉一個可能會讓旁邊的 reachable_pit 變成可達
        while True:
            found_in_loop = False
            relax_unreachable_empty_as_empty()
            for r in range(R):
                for c in range(C):
                    label = bd[r][c]
                    # 判定是否為可直接挖掘的礦
                    is_accessable = False
                    if is_reachable_pit(label) or label == 'pit':
                        is_accessable = True
                    elif label == 'unreachable_pit' and has_empty_neighbor(r, c):
                        is_accessable = True
                    
                    if is_accessable:
                        # 執行採礦
                        gain = REWARD_TABLE.get("pit", 50)
                        cost = 1
                        steps.append({
                            "action": "mine_pit",
                            "target": (r, c),
                            "step_cost": cost,
                            "gain": gain,
                            "path": [(r, c)],
                            "dig_list": [(r, c)]
                        })
                        bd[r][c] = "dug_pit"
                        total_cost += cost
                        total_reward += gain
                        count += 1
                        found_in_loop = True
            if not found_in_loop:
                break
        return count

    # --- 主迴圈 ---
    while True:
        # 1. 優先：把眼前能拿的礦全拿了
        collect_immediate_reachable()

        # 2. 檢查場上是否還有殘存的礦 (unreachable_pit)
        _, _, unrea_pits = list_all_pits(bd)
        
        if not unrea_pits:
            # 場上已無任何形式的 pit，任務完成，準備下樓
            break

        # 3. 尋找路徑：計算到每一個 unreachable_pit 的成本
        dist_map, prev_map = dijkstra_from_all_empties(bd)
        
        # 找出所有礦坑中，成本最低是多少
        min_pit_cost = inf
        reachable_candidates = [] # 儲存 (r, c)

        for (pr, pc) in unrea_pits:
            d = dist_map[pr][pc]
            if d < min_pit_cost:
                min_pit_cost = d
        
        if min_pit_cost == inf:
            # 極端情況：有礦但被牆壁圍死(無路可通)，或者程式邏輯判定那是無法挖掘的區域
            return {
                "ok": False,
                "mode": "v4_strict",
                "message": "有礦坑被完全封死無法抵達，停止規劃。",
                "steps": steps,
                "total_cost": total_cost,
                "total_reward": total_reward,
                "board_after": bd
            }

        # 4. 篩選候選人並隨機選擇 (同成本隨機)
        candidates = [p for p in unrea_pits if dist_map[p[0]][p[1]] == min_pit_cost]
        
        # 這裡就是你要的：隨機選擇
        target = random.choice(candidates)
        
        # 5. 規劃路徑並執行「第一步」
        # 我們只執行挖路的第一格，因為挖開後地圖會變，可能會有更優解或其他礦露出來
        path = reconstruct_path(prev_map, target)
        dig_list, _ = summarize_path(bd, path)
        
        if not dig_list:
            # 照理說不會發生，因為前面 collect_immediate_reachable 應該要處理掉無障礙的礦
            # 這裡做個防呆，如果發生，強制視為採礦
            r, c = target
            bd[r][c] = "dug_pit"
            steps.append({"action": "mine_pit_fallback", "target": target, "step_cost": 1, "gain": 0})
            continue

        first_obs = dig_list[0] # 路徑上的第一個障礙物
        obs_label = bd[first_obs[0]][first_obs[1]]
        obs_cost = enter_cost(obs_label) or 1
        
        steps.append({          
            "action": "mine_path" ,# 為了去礦場而挖的路
            "target": target,      # 最終目標是那個礦
            "step_cost": obs_cost,
            "gain": 0,
            "path": [first_obs],
            "dig_list": [first_obs]
        })
        
        # 更新盤面 (標記為空，雖然實際上可能需要挖多次，但對於規劃來說它即將變成空)
        # 注意：實際執行層 (execute) 會處理點擊次數，這裡只要確保邏輯通暢
        mark_path_as_empty(bd, [first_obs]) 
        total_cost += obs_cost
        
        # 迴圈繼續 -> 回到步驟 1 重新掃描

    # --- 下樓階段 ---
    if descend_after_collect:
        # 所有礦都挖完了，計算去最底層的最短路
        dist_map, prev_map = dijkstra_from_all_empties(bd)
        last_row = R - 1
        
        # 同樣應用「同成本隨機選擇」邏輯於下樓點
        min_floor_cost = inf
        for c in range(C):
            if dist_map[last_row][c] < min_floor_cost:
                min_floor_cost = dist_map[last_row][c]
        
        if min_floor_cost < inf:
            floor_candidates = [c for c in range(C) if dist_map[last_row][c] == min_floor_cost]
            best_c = random.choice(floor_candidates)
            
            end = (last_row, best_c)
            path = reconstruct_path(prev_map, end)
            dig_list, step_cost = summarize_path(bd, path)
            
            steps.append({
                "action": "descend",
                "target": end,
                "step_cost": step_cost,
                "path": path,
                "dig_list": dig_list,
            })
            total_cost += step_cost

    return {
        "ok": True,
        "mode": "collect_all_v4_random",
        "message": f"V4不計成本全清: 成本={int(total_cost)}, 獎勵={int(total_reward)}",
        "steps": steps,
        "total_cost": int(total_cost),
        "total_reward": int(total_reward),
        "board_after": bd,
    }
def plan_collect_all_mines_then_descend_v2(
    board: List[List[str]],
    descend_after_collect: bool = True,
) -> Dict[str, Any]:
    """
        規則：開採所有 pit（可達者優先，dug_pit 已完成不計）。
    策略：
            1) 先開採所有與空地相鄰的 pit（敲擊一次，轉為 dug_pit）。
            2) 若還有 unreachable_pit：用 Dijkstra 找到與當前 empty 區域最近的一個，逐格打通直到可挖，再開採。
      3) 迭代直到所有礦皆被開採。
      4) (可選)最後以最少鏟子成本觸發第 7 層推進盤面。
        回傳 steps 會分成幾種：
            - action="mine_pit"：對可挖的 pit 敲擊一次（dig_list 含 pit 格）。
            - action="mine_path"：為了讓 unreachable_pit 變可達而挖的一格路徑。
            - action="descend"：挖到第 7 層以推進盤面。
    """
    bd = [row[:] for row in board]
    R, C = len(bd), len(bd[0])
    steps: List[Dict[str, Any]] = []
    total_cost = 0
    total_reward = 0

    # 工具：鄰接座標
    def neighbors(r: int, c: int):
        for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
            rr, cc = r+dr, c+dc
            if 0 <= rr < R and 0 <= cc < C:
                yield rr, cc

    # 工具：是否相鄰 empty
    def has_empty_neighbor(r: int, c: int) -> bool:
        return any(is_empty(bd[rr][cc]) for rr, cc in neighbors(r, c))

    # 工具：把與 empty 相連的 unreachable_empty 鬆弛成 empty（避免錯失更短路徑）
    def relax_unreachable_empty_as_empty() -> int:
        changed = 0
        again = True
        while again:
            again = False
            for r in range(R):
                for c in range(C):
                    if bd[r][c] == "unreachable_empty" and has_empty_neighbor(r, c):
                        bd[r][c] = "empty"
                        changed += 1
                        again = True
        if changed:
            print(f"[規劃 v2] 鬆弛 unreachable_empty -> empty 數量: {changed}")
        return changed

    # 先統計所有礦的數量
    dug_initial, rea_initial, unrea_initial = list_all_pits(bd)
    print(f"[規劃 v2] 初始狀態: dug_pit={len(dug_initial)}, reachable_pit={len(rea_initial)}, unreachable_pit={len(unrea_initial)}")
    if unrea_initial:
        print(f"  unreachable_pit 位置: {unrea_initial}")

    def collect_reachable_pits_once() -> int:
        """把目前盤面上所有 reachable 的 pit 以敲擊一次挖開（變 dug_pit），回傳採集數。"""
        dug_cnt = 0
        for r in range(R):
            for c in range(C):
                if is_reachable_pit(bd[r][c]) or bd[r][c] == "pit":
                    pit_cost = 1
                    steps.append({
                        "action": "mine_pit",
                        "target": (r, c),
                        "step_cost": pit_cost,
                        "gain": REWARD_TABLE.get("pit", REWARD_TABLE.get("reachable_pit", 0)),
                        "path": [(r, c)],
                        "dig_list": [(r, c)],
                    })
                    total_reward_nonlocal[0] += REWARD_TABLE.get("pit", REWARD_TABLE.get("reachable_pit", 0))
                    total_cost_nonlocal[0] += pit_cost
                    bd[r][c] = "dug_pit"
                    dug_cnt += 1
        return dug_cnt

    def collect_adjacent_unreachable_pits_once() -> int:
        """把目前『貼著 empty』的 unreachable_pit 挖開（敲擊一次），回傳採集數。"""
        dug_cnt = 0
        for r in range(R):
            for c in range(C):
                if bd[r][c] == "unreachable_pit" and has_empty_neighbor(r, c):
                    pit_cost = 1
                    steps.append({
                        "action": "mine_pit",
                        "target": (r, c),
                        "step_cost": pit_cost,
                        "gain": REWARD_TABLE.get("pit", REWARD_TABLE.get("unreachable_pit", 0)),
                        "path": [(r, c)],
                        "dig_list": [(r, c)],
                    })
                    bd[r][c] = "dug_pit"
                    total_reward_nonlocal[0] += REWARD_TABLE.get("pit", REWARD_TABLE.get("unreachable_pit", 0))
                    total_cost_nonlocal[0] += pit_cost
                    dug_cnt += 1
        return dug_cnt

    # 用 list 包住,讓內部函式可寫外層變數
    total_reward_nonlocal = [0]
    total_cost_nonlocal = [0]

    # 1) 先把所有目前可開採的礦收掉（包含貼著 empty 的 unreachable_pit）
    while True:
        changed = 0
        changed += collect_reachable_pits_once()
        # 貼邊的 unreachable_pit 也直接收
        changed += collect_adjacent_unreachable_pits_once()
        # 鬆弛可能因此曝光的 unreachable_empty
        changed += relax_unreachable_empty_as_empty()
        if changed == 0:
            break

    # 2) 若還有 unreachable_pit，就逐步打通最容易連通的那一個：
    #    一次只挖『一格』，每挖一格就鬆弛 unreachable_void 並嘗試開採貼邊礦，避免錯過更短路徑
    while True:
        _, _, unrea = list_all_pits(bd)
        if not unrea:
            print(f"[規劃 v2] 所有礦已開採完畢")
            break  # 全部收完
        
        print(f"[規劃 v2] 剩餘 unreachable_pit: {len(unrea)} 個，位置: {unrea}")

        # 用 Dijkstra 算出到每格的最小鏟子成本(源點=所有 empty+dug_pit)
        dist, prev = dijkstra_from_all_empties(bd)

        # 找最容易連通的一個 unreachable_pit(取其 dist 最小)
        best_pos, best_cost = None, inf
        for (mr, mc) in unrea:
            d = dist[mr][mc]
            if d < best_cost:
                best_cost = d
                best_pos = (mr, mc)
        if best_pos is None or best_cost == inf:
            # 無法連通剩餘礦
            return {
                "ok": False,
                "mode": "collect_all_then_descend_v2",
                "message": "有礦無法連通(可能被 unreachable_empty 圍死)。",
                "steps": steps,
                "total_cost": int(total_cost_nonlocal[0]),
                "total_reward": int(total_reward_nonlocal[0]),
                "board_after": bd,
            }

        # 回溯路徑，『僅挖第一格』，後續改由下一輪重新規劃
        path = reconstruct_path(prev, best_pos)
        dig_list, step_cost = summarize_path(bd, path)
        if not dig_list:
            # 已可直接挖該礦（敲擊一次）
            r, c = best_pos
            pit_cost = 1
            steps.append({
                "action": "mine_pit",
                "target": best_pos,
                "step_cost": pit_cost,
                "gain": REWARD_TABLE.get("pit", REWARD_TABLE.get("unreachable_pit", 0)),
                "path": [(r, c)],
                "dig_list": [(r, c)],
            })
            bd[r][c] = "dug_pit"
            total_reward_nonlocal[0] += REWARD_TABLE.get("pit", REWARD_TABLE.get("unreachable_pit", 0))
            total_cost_nonlocal[0] += pit_cost
            # 再嘗試鬆弛/開採一次
            relax_unreachable_empty_as_empty()
            collect_reachable_pits_once()
            collect_adjacent_unreachable_pits_once()
            continue

        # 只挖第一格
        first_cell = dig_list[0]
        first_cost = enter_cost(bd[first_cell[0]][first_cell[1]]) or 0
        steps.append({
            "action": "mine_path",
            "target": best_pos,
            "step_cost": first_cost,
            "gain": None,
            "path": [first_cell],
            "dig_list": [first_cell],
        })
        mark_path_as_empty(bd, [first_cell])
        total_cost_nonlocal[0] += first_cost

        # 挖一格後，鬆弛 unreachable_empty，並嘗試開採貼邊礦
        relax_unreachable_empty_as_empty()
        collect_reachable_pits_once()
        collect_adjacent_unreachable_pits_once()

    # 3) 全部礦收完後,依需求下樓
    if descend_after_collect:
        # 先嘗試鬆弛一次 unreachable_empty（可能挖礦過程中已經讓某些格可達）
        relax_unreachable_empty_as_empty()
        dist, prev = dijkstra_from_all_empties(bd)
        last_row = R - 1
        best_c, best_floor_cost = None, inf
        for c in range(C):
            # 任何可達的格子都可以當下樓點
            if dist[last_row][c] < best_floor_cost:
                best_floor_cost = dist[last_row][c]
                best_c = c
        if best_c is not None and best_floor_cost < inf:
            end = (last_row, best_c)
            path = reconstruct_path(prev, end)
            dig_list, step_cost = summarize_path(bd, path)
            # 即使 step_cost = 0（已經可以直接下樓），也要添加步驟告訴程式該下樓了
            steps.append({
                "action": "descend",
                "target": end,
                "step_cost": step_cost,
                "path": path,
                "dig_list": dig_list,
            })
            mark_path_as_empty(bd, dig_list)
            total_cost_nonlocal[0] += step_cost

    return {
        "ok": True,
        "mode": "collect_all_then_descend_v2",
        "message": f"總成本={int(total_cost_nonlocal[0])}, 全部已開採=True, 總獎勵={int(total_reward_nonlocal[0])}",
        "steps": steps,
        "total_cost": int(total_cost_nonlocal[0]),
        "total_reward": int(total_reward_nonlocal[0]),
        "board_after": bd,
    }
def pixel_match(bgr_actual, bgr_expected, tol=0):
    diff = np.abs(bgr_actual.astype(int) - np.array(bgr_expected, dtype=int))
    return np.all(diff <= tol)

def check_points(img, expected_points, tol=0, min_required=3, verbose=True):
    #如果是opencv 不執行
    
    img = to_bgr_np(img)
    matched = []
    for (x, y), bgr_exp in expected_points:
        # OpenCV 取像素：img[y, x]
        bgr_act = img[y, x]
        ok = pixel_match(bgr_act, bgr_exp, tol=tol)
        matched.append(ok)
        if verbose:
            rgb_act = tuple(int(v) for v in bgr_act[::-1])
            # print(f"座標=({x}, {y})  實際BGR={tuple(int(v) for v in bgr_act)} "
            #       f"RGB={rgb_act}  期望BGR={bgr_exp}  => {ok}")
    n_ok = sum(matched)
    if verbose:
        print(f"結果：符合 {n_ok}/{len(expected_points)} 個點（門檻 {min_required}，容忍度 ±{tol}）")
    return n_ok >= min_required, n_ok


def check_pickaxe_count(d)->int:
    try:
        img = d.screenshot(format='opencv')[13:40,148:251]
        img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, img_bin = cv2.threshold(img_gray, 150, 255, cv2.THRESH_BINARY_INV)
        result = analyze_skill_via_http(img_bin)
        result =result.get("ocr_results",None)[0]
        print("OCR Result:", result)

        if result is not None:
            try:
                text = result.get("text",None).split("/")[0]
                return int(text)
            except ValueError:
                pass
        else:
            return 20
        print("OCR 結果格式不符，回傳預設值 50")
        #保存圖片後續分析
        if not os.path.exists("ocr_errors"):
            os.makedirs("ocr_errors")
        timestamp = int(time.time())
        error_image_path = os.path.join("ocr_errors", f"ocr_error_{timestamp}.png")
        # cv2.imwrite(error_image_path, img)
        return 50
    except Exception as e:
        print("OCR 未返回有效結果或解析失敗，回傳預設值 20")
        return 20
import re
def run(d:u2.Device, ip, clf:ClassifierCNN, rl_recorder: Optional[RLRecorder] = None, max_duration_minutes: float = 8.0):
    """
    執行挖礦流程
    
    Args:
        d: uiautomator2 設備
        ip: 設備 IP
        clf: CNN 分類器
        rl_recorder: RL 記錄器（可選）
        max_duration_minutes: 最長執行時間（分鐘），預設 5 分鐘
    """
    # 記錄開始時間
    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    print(f"⏱️ 開始挖礦，時間限制: {max_duration_minutes} 分鐘")
    
    # 2. 截圖並分類
    count = check_pickaxe_count(d)
    if count < 5:
        print("鏟子數量過少，停止挖礦")
        return
    retry_count = 0
    while count >= 1:
        # 檢查是否超過時間限制
        elapsed_time = time.time() - start_time
        if elapsed_time >= max_duration_seconds:
            elapsed_minutes = elapsed_time / 60
            print(f"⏱️ 已達時間限制 ({elapsed_minutes:.1f} 分鐘)，停止挖礦")
            break
        passed, n_ok = check_points(d.screenshot(format="opencv"), expected_points, tol=TOL, min_required=MIN_REQUIRED)
        if passed:
            d.click(444+random.randint(-10,10),107+random.randint(-10,10))  # 點擊畫面中間偏上位置，關閉可能的干擾UI
            continue
        img = d.screenshot()  # 回傳為 numpy array
       
        # 只在初始規劃時保存低信心度樣本，避免截到動畫畫面
        start = time.time()
        board, confidences = clf.classify_board(img, save_samples=True, save_conf_threshold=0.98)
        end = time.time()
        print(f"分類盤面耗時: {end - start:.2f} 秒")
        # # 先產生道具建議，若有達門檻則優先執行道具，否則執行基線規劃
        # items_available = {"drill": 1, "bomb": 1}
        # t0 = time.time()
        # item_plan = plan_with_items_ev(board, items_available, drill_threshold=2.0, bomb_threshold=3.0)
        # t1 = time.time()
        # print(f"道具建議耗時: {t1 - t0:.2f} 秒")
        # print_plan_result("道具建議 (可能執行)", item_plan, board)

        # if item_plan.get("ok") and item_plan.get("mode") == "item_ev_plan" and item_plan.get("steps"):
        #     # 執行道具：execute_plan_steps 會在使用道具後自動 return 以便重新規劃
        #     start = time.time()
        #     execute_plan_steps(d, clf, board, item_plan["steps"], rl_recorder=rl_recorder)
        #     end = time.time()
        #     print(f"執行道具步驟耗時: {end - start:.2f} 秒")
        #     # 道具不消耗鏟子；重新迴圈以重新分類與規劃
        #     continue

        # 沒有適合的道具 → 執行基線規劃
        baseline_plan = plan_collect_all_mines_then_descend_v2(board, descend_after_collect=True)
        print_plan_result("基線規劃 (執行)", baseline_plan, board)

        if baseline_plan.get("ok") and baseline_plan.get("steps"):
            start = time.time()
            execute_plan_steps(d, clf, board, baseline_plan["steps"], rl_recorder=rl_recorder)
            end = time.time()
            print(f"執行挖礦步驟耗時: {end - start:.2f} 秒")
            count -= baseline_plan.get("total_cost", 0)
            print(f"剩餘鏟子數量: {count}")
        else:
            print("⚠️ 基線規劃無步驟可執行，停止挖礦")
            print(f"訊息: {baseline_plan.get('message', '未知')}")
            break
    # 結束後 flush 記錄
    if rl_recorder:
        rl_recorder.flush()
        summary = rl_recorder.summary()
        print(f"\n[RL 記錄] 共 {summary['total']} 筆事件，檔案: {summary['log_path']}")
# ====== 範例：把「規劃B」轉成實際操作 ======
if __name__ == '__main__':
    d = u2.connect('emulator-5554')  # 你的裝置 ID
    # model, classes, device = load_cnn_model()
    # clf = ClassifierCNN(model=model, classes=classes, device=device, dataset_root="dataset/low_confidence")
    
    # # 記錄但不自動訓練
    # rl_logs_dir = os.path.join(os.path.dirname(__file__), "rl_logs")
    # rl_recorder = RLRecorder(
    #     log_dir=rl_logs_dir,
    #     auto_train=False,  # 不自動訓練
    #     flush_interval=5,
    # )
    # img = d.screenshot(format='opencv')[13:40,148:251]
    # img_gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # _, img_bin = cv2.threshold(img_gray, 150, 255, cv2.THRESH_BINARY_INV)
    # print(check_pickaxe_count(d))

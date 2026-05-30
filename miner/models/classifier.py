"""負責載入 CNN 模型並提供盤面分類能力。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from miner.core.config import DEFAULT_CLASSES, DEFAULT_CNN_MODEL, GRID_CFG
from utils.torch_runtime import inference_slot
from .simplecnn import SimpleCNN, resize_size

Board = List[List[str]]
ConfidenceGrid = List[List[float]]


def _resolve_device(device: Optional[Union[str, torch.device]]) -> torch.device:
    """根據輸入字串或現有模型推算要使用的裝置。"""
    if device is None:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device) if isinstance(device, str) else device


def load_cnn_model(
    model_path: Optional[str] = None,
    device: Optional[Union[str, torch.device]] = None,
) -> Tuple[SimpleCNN, List[str], torch.device]:
    """載入訓練好的模型檔，並回傳模型、類別清單與裝置。"""
    resolved_device = _resolve_device(device)
    path = model_path or DEFAULT_CNN_MODEL
    checkpoint = torch.load(path, map_location=resolved_device)

    classes = list(checkpoint.get("classes", DEFAULT_CLASSES)) or list(DEFAULT_CLASSES)
    model = SimpleCNN(num_classes=len(classes))
    model.load_state_dict(checkpoint["model_state"])
    model.to(resolved_device)
    model.eval()
    setattr(model, "classes", classes)

    print(f"[CNN Loader] Loaded model from {path}")
    print(f"[CNN Loader] Device: {resolved_device}")
    print(f"[CNN Loader] Classes: {classes}")
    return model, classes, resolved_device


@dataclass
class ClassifierCNN:
    """提供 `classify_board` 方法將截圖切格、辨識並回傳盤面標籤。"""

    model: SimpleCNN
    classes: Optional[Sequence[str]] = None
    device: Optional[Union[str, torch.device]] = None
    dataset_root: Optional[str] = None

    def __post_init__(self) -> None:
        if self.model is None:
            raise ValueError("ClassifierCNN requires a pre-loaded model instance.")

        if self.device is None:
            try:
                resolved_device = next(self.model.parameters()).device
            except StopIteration:
                resolved_device = _resolve_device(None)
        else:
            resolved_device = _resolve_device(self.device)

        self.device = resolved_device
        self.model = self.model.to(self.device)
        self.model.eval()

        if self.classes:
            self.classes = list(self.classes)
        else:
            model_classes = getattr(self.model, "classes", None)
            self.classes = list(model_classes) if model_classes else list(DEFAULT_CLASSES)

        if not self.classes:
            raise ValueError("ClassifierCNN requires class labels; provide via parameter or set model.classes.")

        self.skipped_samples: Dict[str, int] = {cls: 0 for cls in self.classes}
        self.transform = transforms.Compose(
            [
                transforms.Resize(resize_size),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )

        print(f"[CNN Classifier] Device: {self.device}")
        print(f"[CNN Classifier] Classes: {self.classes}")

    # pylint: disable=too-many-locals
    def classify_board(
        self,
        img: Union[str, np.ndarray, Image.Image],
        save_samples: bool = True,
        save_conf_threshold: float = 0.8,
    ) -> Tuple[Board, ConfidenceGrid]:
        """將傳入圖片切成 7x6 的格子並逐格推論。"""
        if isinstance(img, str):
            img = cv2.imread(img)
        elif isinstance(img, Image.Image):
            arr = np.array(img)
            if arr.ndim == 2:
                arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            else:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            img = arr

        if img is None:
            raise ValueError("image not found or unreadable")

        H, W = GRID_CFG["H"], GRID_CFG["W"]
        x0, y0, x1, y1 = GRID_CFG["x0"], GRID_CFG["y0"], GRID_CFG["x1"], GRID_CFG["y1"]
        cell_w = int(round((x1 - x0) / W))
        cell_h = int(round((y1 - y0) / H))

        # 預處理所有格子，再以「單次批次 forward」推論整個盤面。
        # SimpleCNN 無 BatchNorm/Dropout，eval 下逐格與批次數學等價，
        # 但只跑一次 forward 大幅省去每格的 kernel launch / 呼叫開銷。
        cells: List[np.ndarray] = []
        cell_tensors: List[torch.Tensor] = []
        for r in range(H):
            for c in range(W):
                cx0 = x0 + c * cell_w
                cy0 = y0 + r * cell_h
                cell = img[cy0 : cy0 + cell_h, cx0 : cx0 + cell_w]

                cell_rgb = cv2.cvtColor(cell, cv2.COLOR_BGR2RGB)
                cell_pil = Image.fromarray(cell_rgb)

                cells.append(cell)
                cell_tensors.append(self.transform(cell_pil))

        # inference_slot() 序列化共用模型的 forward，讓多裝置同時推論時排隊
        # 而非一起擠爆 GPU（分流）。
        with inference_slot(), torch.inference_mode():
            batch = torch.stack(cell_tensors).to(self.device)
            outputs = self.model(batch)
            probs = torch.nn.functional.softmax(outputs, dim=1)
            conf_t, pred_t = torch.max(probs, dim=1)
        conf_flat: List[float] = conf_t.tolist()
        pred_flat: List[int] = pred_t.tolist()

        board: Board = []
        confidences: ConfidenceGrid = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        for r in range(H):
            row: List[str] = []
            conf_row: List[float] = []
            for c in range(W):
                idx = r * W + c
                label = self.classes[pred_flat[idx]]
                confidence = conf_flat[idx]

                row.append(label)
                conf_row.append(confidence)

                # 修改儲存樣本的條件：除了原本的 0.95-0.99，也儲存 0.6-0.8 的低信心度樣本
                # 這主要用於收集難以辨識的樣本，以便重新訓練
                should_save = False
                if save_samples and self.dataset_root:
                     # 條件 1: 既有的高信心度樣本收集
                     if 0.95 <= confidence <= 0.99:
                         should_save = True
                     # 條件 2: 使用者要求的高價值低信心度區間
                     elif 0.6 <= confidence <= 0.8:
                         should_save = True

                if should_save and self.skipped_samples.get(label, 0) == 0:
                    label_dir = os.path.join(self.dataset_root, label)
                    os.makedirs(label_dir, exist_ok=True)
                    # 針對低信心度樣本，可以考慮放寬數量限制或放入不同資料夾，這裡暫時共用
                    if len(os.listdir(label_dir)) >= 1000:
                        self.skipped_samples[label] = self.skipped_samples.get(label, 0) + 1
                        continue
                    fname = f"{timestamp}_r{r}_c{c}_{label}_conf{confidence:.4f}.png"
                    cv2.imwrite(os.path.join(label_dir, fname), cells[idx])

            board.append(row)
            confidences.append(conf_row)

        return board, confidences


__all__ = ["ClassifierCNN", "load_cnn_model"]

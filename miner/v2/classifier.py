from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from miner.core.config import DEFAULT_CLASSES, DEFAULT_CNN_MODEL, GRID_CFG
from miner.core.vision_utils import check_points, to_bgr_np
from miner.models.simplecnn import SimpleCNN, resize_size
from .types import Board, BoardSnapshot, ConfidenceGrid, ScreenCheckResult


def _resolve_device(device: Optional[Union[str, torch.device]]) -> torch.device:
    if device is None:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(device) if isinstance(device, str) else device


def load_board_model(
    model_path: Optional[str] = None,
    device: Optional[Union[str, torch.device]] = None,
) -> Tuple[SimpleCNN, list[str], torch.device]:
    resolved_device = _resolve_device(device)
    checkpoint = torch.load(model_path or DEFAULT_CNN_MODEL, map_location=resolved_device)

    classes = list(checkpoint.get("classes", DEFAULT_CLASSES)) or list(DEFAULT_CLASSES)
    model = SimpleCNN(num_classes=len(classes))
    model.load_state_dict(checkpoint["model_state"])
    model.to(resolved_device)
    model.eval()
    setattr(model, "classes", classes)
    return model, classes, resolved_device


@dataclass
class BoardClassifierV2:
    model: SimpleCNN
    classes: Optional[Sequence[str]] = None
    device: Optional[Union[str, torch.device]] = None
    grid_config: Optional[Dict[str, int]] = None
    dataset_root: Optional[str] = None

    def __post_init__(self) -> None:
        if self.model is None:
            raise ValueError("BoardClassifierV2 requires a pre-loaded model instance.")

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
        self.grid_config = dict(self.grid_config or GRID_CFG)
        self.classes = list(self.classes or getattr(self.model, "classes", DEFAULT_CLASSES))
        if not self.classes:
            raise ValueError("BoardClassifierV2 requires non-empty class labels.")

        self.transform = transforms.Compose(
            [
                transforms.Resize(resize_size),
                transforms.ToTensor(),
                transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
            ]
        )

    def classify_board(
        self,
        image: Union[str, np.ndarray, Image.Image],
        save_samples: bool = False,
        save_conf_threshold: float = 0.8,
    ) -> Tuple[Board, ConfidenceGrid]:
        frame = _to_bgr_image(image)
        height, width = self.grid_config["H"], self.grid_config["W"]
        x0 = self.grid_config["x0"]
        y0 = self.grid_config["y0"]
        x1 = self.grid_config["x1"]
        y1 = self.grid_config["y1"]
        cell_w = int(round((x1 - x0) / width))
        cell_h = int(round((y1 - y0) / height))

        board: Board = []
        confidences: ConfidenceGrid = []
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        with torch.no_grad():
            for row_index in range(height):
                board_row: list[str] = []
                confidence_row: list[float] = []
                for col_index in range(width):
                    crop_x0 = x0 + col_index * cell_w
                    crop_y0 = y0 + row_index * cell_h
                    crop_x1 = min(crop_x0 + cell_w, frame.shape[1])
                    crop_y1 = min(crop_y0 + cell_h, frame.shape[0])
                    if crop_x1 <= crop_x0 or crop_y1 <= crop_y0:
                        raise ValueError(
                            f"invalid crop for cell ({row_index}, {col_index}) with image shape {frame.shape}"
                        )

                    cell = frame[crop_y0:crop_y1, crop_x0:crop_x1]
                    label, confidence = self._predict_cell(cell)
                    board_row.append(label)
                    confidence_row.append(confidence)
                    if save_samples and self.dataset_root and confidence <= save_conf_threshold:
                        self._save_sample(cell, timestamp, row_index, col_index, label, confidence)

                board.append(board_row)
                confidences.append(confidence_row)

        return board, confidences

    def classify_snapshot(
        self,
        image: Union[str, np.ndarray, Image.Image],
        verify_screen: bool = True,
        save_samples: bool = False,
        save_conf_threshold: float = 0.8,
    ) -> BoardSnapshot:
        frame = _to_bgr_image(image)
        board, confidences = self.classify_board(
            frame,
            save_samples=save_samples,
            save_conf_threshold=save_conf_threshold,
        )

        screen_check = None
        if verify_screen:
            passed, matched_points = check_points(frame)
            screen_check = ScreenCheckResult(passed=passed, matched_points=matched_points)

        return BoardSnapshot(
            board=board,
            confidences=confidences,
            captured_at=datetime.now().isoformat(timespec="seconds"),
            image_shape=tuple(frame.shape),
            grid_config=dict(self.grid_config),
            screen_check=screen_check,
        )

    def _predict_cell(self, cell: np.ndarray) -> Tuple[str, float]:
        cell_rgb = cv2.cvtColor(cell, cv2.COLOR_BGR2RGB)
        cell_pil = Image.fromarray(cell_rgb)
        cell_tensor = self.transform(cell_pil).unsqueeze(0).to(self.device)
        output = self.model(cell_tensor)
        probs = torch.nn.functional.softmax(output, dim=1)
        confidence, predicted_index = torch.max(probs, dim=1)
        label = self.classes[predicted_index.item()]
        return label, confidence.item()

    def _save_sample(
        self,
        cell: np.ndarray,
        timestamp: str,
        row_index: int,
        col_index: int,
        label: str,
        confidence: float,
    ) -> None:
        label_dir = Path(self.dataset_root) / label
        label_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{timestamp}_r{row_index}_c{col_index}_{label}_conf{confidence:.4f}.png"
        cv2.imwrite(str(label_dir / filename), cell)


def _to_bgr_image(image: Union[str, np.ndarray, Image.Image]) -> np.ndarray:
    if isinstance(image, str):
        frame = cv2.imread(image)
    elif isinstance(image, (Image.Image, np.ndarray)):
        frame = to_bgr_np(image)
    else:
        raise TypeError(f"Unsupported image type: {type(image)!r}")

    if frame is None:
        raise ValueError("image not found or unreadable")
    return frame

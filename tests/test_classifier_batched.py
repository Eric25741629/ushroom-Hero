"""Batched-inference contract for the mining block classifiers.

`classify_board` historically ran one forward pass per cell (H*W = 42
forwards per call, plus 3-5 re-classifies per dug cell in the executor).
These tests pin the optimized contract:

  * the model is invoked exactly ONCE per `classify_board` call -- all
    cells are stacked into a single batched forward pass, and
  * the batched result is numerically equivalent to the per-cell
    reference loop (identical labels, confidences within fp tolerance).

`SimpleCNN` has no BatchNorm / Dropout, so each sample's math is
independent of batch composition; in eval mode, batching is
mathematically equivalent. Tests run on CPU for determinism.
"""
from __future__ import annotations

from typing import List, Tuple

import cv2
import numpy as np
import torch
from PIL import Image
from torchvision import transforms

from miner.core.config import GRID_CFG
from miner.models.classifier import ClassifierCNN
from miner.models.simplecnn import SimpleCNN, resize_size
from miner.v2.classifier import BoardClassifierV2

CLASSES = ["dirt", "empty", "rock", "reachable_pit"]


class _CountingCNN(torch.nn.Module):
    """Wraps a real SimpleCNN and counts forward invocations."""

    def __init__(self, inner: SimpleCNN) -> None:
        super().__init__()
        self.inner = inner
        self.forward_calls = 0

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        self.forward_calls += 1
        return self.inner(x)


def _make_model(seed: int = 0) -> SimpleCNN:
    torch.manual_seed(seed)
    model = SimpleCNN(num_classes=len(CLASSES))
    model.eval()
    return model


def _make_image(seed: int = 1) -> np.ndarray:
    """Synthetic BGR frame covering the full grid region (x1=535, y1=852)."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(960, 540, 3), dtype=np.uint8)


def _reference_board(model: torch.nn.Module, image: np.ndarray) -> Tuple[list, list]:
    """Independent per-cell reference -- the pre-optimization algorithm."""
    transform = transforms.Compose(
        [
            transforms.Resize(resize_size),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ]
    )
    height, width = GRID_CFG["H"], GRID_CFG["W"]
    x0, y0, x1, y1 = GRID_CFG["x0"], GRID_CFG["y0"], GRID_CFG["x1"], GRID_CFG["y1"]
    cell_w = int(round((x1 - x0) / width))
    cell_h = int(round((y1 - y0) / height))

    board: List[List[str]] = []
    confs: List[List[float]] = []
    with torch.no_grad():
        for r in range(height):
            row: List[str] = []
            conf_row: List[float] = []
            for c in range(width):
                cx0 = x0 + c * cell_w
                cy0 = y0 + r * cell_h
                cell = image[cy0 : cy0 + cell_h, cx0 : cx0 + cell_w]
                cell_rgb = cv2.cvtColor(cell, cv2.COLOR_BGR2RGB)
                cell_pil = Image.fromarray(cell_rgb)
                tensor = transform(cell_pil).unsqueeze(0)
                output = model(tensor)
                probs = torch.nn.functional.softmax(output, dim=1)
                conf, idx = torch.max(probs, dim=1)
                row.append(CLASSES[idx.item()])
                conf_row.append(conf.item())
            board.append(row)
            confs.append(conf_row)
    return board, confs


def _assert_boards_equivalent(board, confs, ref_board, ref_confs) -> None:
    assert board == ref_board
    flat = [v for row in confs for v in row]
    ref_flat = [v for row in ref_confs for v in row]
    assert np.allclose(flat, ref_flat, atol=1e-5)


# ----- v1: miner/models/classifier.py -----


def test_v1_classify_board_runs_single_forward_pass():
    counting = _CountingCNN(_make_model())
    clf = ClassifierCNN(model=counting, classes=CLASSES, device="cpu")
    image = _make_image()

    clf.classify_board(image, save_samples=False)

    assert counting.forward_calls == 1


def test_v1_classify_board_matches_per_cell_reference():
    image = _make_image()
    ref_board, ref_confs = _reference_board(_make_model(), image)

    clf = ClassifierCNN(model=_make_model(), classes=CLASSES, device="cpu")
    board, confs = clf.classify_board(image, save_samples=False)

    _assert_boards_equivalent(board, confs, ref_board, ref_confs)


# ----- v2: miner/v2/classifier.py -----


def test_v2_classify_board_runs_single_forward_pass():
    counting = _CountingCNN(_make_model())
    clf = BoardClassifierV2(model=counting, classes=CLASSES, device="cpu")
    image = _make_image()

    clf.classify_board(image, save_samples=False)

    assert counting.forward_calls == 1


def test_v2_classify_board_matches_per_cell_reference():
    image = _make_image()
    ref_board, ref_confs = _reference_board(_make_model(), image)

    clf = BoardClassifierV2(model=_make_model(), classes=CLASSES, device="cpu")
    board, confs = clf.classify_board(image, save_samples=False)

    _assert_boards_equivalent(board, confs, ref_board, ref_confs)

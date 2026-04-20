from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .classifier import BoardClassifierV2
from .types import BoardSnapshot

if TYPE_CHECKING:
    import uiautomator2 as u2

    DeviceLike = u2.Device
else:
    DeviceLike = Any


def classify_frame(
    frame,
    classifier: BoardClassifierV2,
    verify_screen: bool = True,
    save_samples: bool = False,
    save_conf_threshold: float = 0.8,
) -> BoardSnapshot:
    return classifier.classify_snapshot(
        frame,
        verify_screen=verify_screen,
        save_samples=save_samples,
        save_conf_threshold=save_conf_threshold,
    )


def capture_board_snapshot(
    device: DeviceLike,
    classifier: BoardClassifierV2,
    verify_screen: bool = True,
    screenshot_format: str = "opencv",
    save_samples: bool = False,
    save_conf_threshold: float = 0.8,
) -> BoardSnapshot:
    frame = device.screenshot(format=screenshot_format)
    return classify_frame(
        frame,
        classifier=classifier,
        verify_screen=verify_screen,
        save_samples=save_samples,
        save_conf_threshold=save_conf_threshold,
    )

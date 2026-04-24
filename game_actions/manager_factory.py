"""Per-device runtime manager factory.

Extracted from new_main_v2.main() as Phase 9 of the slim-down plan.
Called once per device after successful device initialisation.
"""
from __future__ import annotations

import os
from typing import Any, Tuple

from Spin_Wheel import spin_wheel
from Mission import mission
from family import Family_manager
from State import state
from miner.models.classifier import ClassifierCNN
from miner.rl.rl_recorder import RLRecorder
from config.paths import DATASET_LOW_CONFIDENCE_DIR_STR


def _init_runtime_managers(
    d: Any,
    ip: str,
    Cnn_model: Any,
    oralce_cnn_model: Any,
    oralce_classes: Any,
) -> Tuple[Any, Any, Any, Any, Any, Any]:
    """Create all per-device manager objects.

    Returns (wheel_manager, mission_manager, family_manager,
             state_manager, clf, rl_recorder).
    """
    wheel_manager = spin_wheel(device=d, cnn_model=Cnn_model, devices_serial=ip)
    mission_manager = mission(device=d, ip=ip)
    family_manager = Family_manager(device=d, ip=ip, cnn_model=Cnn_model)
    state_manager = state(device=d, cnn_model=Cnn_model)
    clf = ClassifierCNN(
        model=oralce_cnn_model,
        classes=oralce_classes,
        dataset_root=DATASET_LOW_CONFIDENCE_DIR_STR,
    )
    rl_logs_dir = os.path.join("miner", "rl_logs", ip.replace(":", "_"))
    os.makedirs(rl_logs_dir, exist_ok=True)
    rl_recorder = RLRecorder(log_dir=rl_logs_dir, auto_train=False, flush_interval=1)
    return wheel_manager, mission_manager, family_manager, state_manager, clf, rl_recorder

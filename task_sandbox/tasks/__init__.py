"""TaskSpec registry - populated at import time by individual task modules."""
from __future__ import annotations

from task_sandbox.spec import TaskSpec

from . import lamp as _lamp_module

TASK_REGISTRY: dict[str, TaskSpec] = {
    _lamp_module.LAMP.name: _lamp_module.LAMP,
}

__all__ = ["TASK_REGISTRY"]

"""LAMP TaskSpec - wraps opengold_v2.LampService zero-modification."""
from __future__ import annotations

from task_sandbox import EveryHours, NavTarget, TaskResult, TaskSpec
from task_sandbox.spec import TaskContext


def _lamp_runner(ctx: TaskContext) -> TaskResult:
    from opengold_v2 import LampService, OpenGoldConfig

    cfg = OpenGoldConfig()
    svc = LampService(ctx.device, cfg, device_ip=ctx.ip)
    times = int(ctx.config.get("lamp_times", 1000))
    try:
        svc.run(times=times, is_compare=True)
    except Exception as e:
        return TaskResult(ok=False, reason=f"lamp_service_exc:{type(e).__name__}:{e}")
    return TaskResult(ok=True)


def _lamp_enabled(ip: str) -> bool:
    try:
        import config_manager
        return bool(config_manager.get_device_config(ip).get("lamp_check_interval"))
    except Exception:
        return True


LAMP = TaskSpec(
    name="lamp",
    entry=NavTarget.LAMP_PAGE,
    schedule=EveryHours(hours=2),
    runner=_lamp_runner,
    enabled_when=_lamp_enabled,
    references=(
        "opengold_v2/lamp_service.py",
        "opengold_v2/ui_controller.py",
        "Open_gold_paddle_ocr.py",
    ),
)

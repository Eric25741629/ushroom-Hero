"""task_sandbox CLI - `python -m task_sandbox <subcommand>`."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .runner import run_task
from .spec import TaskContext
from .tasks import TASK_REGISTRY
from .trace.recorder import Recorder


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="task_sandbox")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list registered TaskSpecs")

    run_p = sub.add_parser("run", help="run a single task end-to-end")
    run_p.add_argument("task", help="task name (see `list`)")
    run_p.add_argument("--device", required=True, help="adb serial / device ip")
    run_p.add_argument("--out", default="runs", help="output dir for trace + screenshots")
    run_p.add_argument("--timeout", type=float, default=600.0)

    return p


def run_list(_args: argparse.Namespace) -> int:
    for name, spec in sorted(TASK_REGISTRY.items()):
        print(f"{name:<24} entry={spec.entry.value:<12} schedule={type(spec.schedule).__name__}")
    return 0


def _build_runtime_context(
    device_ip: str,
    out_root: Path,
    task_name: str,
    timeout_sec: float,
) -> tuple[TaskContext, Any]:
    """Connect to a real device and produce a TaskContext + stage_resolver.

    Imports adb_operations / config_manager / cnn_model lazily so unit
    tests of the CLI parser don't require those heavy modules.
    """
    from adb_operations import connect_u2_with_retries
    from device_wrapper import MonitoredDevice
    from game_actions.stage_guard import get_stage_with_check
    import config_manager
    from utils.model_sync import ensure_local_model
    import new_cnn.cnn_model as cnn_model

    raw = connect_u2_with_retries(device_ip)
    device = MonitoredDevice(raw, device_ip)

    local_pth = ensure_local_model("cnn_model.pth")
    cnn = cnn_model.load_cnn_model(local_pth)

    run_id = f"{task_name}_{time.strftime('%Y-%m-%d_%H-%M-%S')}_{device_ip}"
    run_dir = out_root / run_id
    rec = Recorder(run_dir)
    rec.bind_device(device)

    cfg = config_manager.get_device_config(device_ip) or {}

    ctx = TaskContext(
        device=device,
        ip=device_ip,
        cnn_model=cnn,
        recorder=rec,
        config=dict(cfg),
        timeout_at=time.time() + timeout_sec,
    )

    def stage_resolver(_ctx: TaskContext) -> str:
        return get_stage_with_check(_ctx.device, _ctx.ip, _ctx.cnn_model)

    return ctx, stage_resolver


def run_run(args: argparse.Namespace) -> int:
    if args.task not in TASK_REGISTRY:
        print(
            f"unknown task: {args.task!r}; known: {sorted(TASK_REGISTRY)}",
            file=sys.stderr,
        )
        return 2

    spec = TASK_REGISTRY[args.task]
    ctx, stage_resolver = _build_runtime_context(
        args.device, Path(args.out), args.task, args.timeout,
    )

    try:
        result = run_task(spec, ctx, stage_resolver=stage_resolver)
    finally:
        ctx.recorder.close()

    print(f"task={args.task} ok={result.ok} reason={result.reason!r}")
    print(f"trace: {ctx.recorder.run_dir}/trace.jsonl")
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "list":
        return run_list(args)
    if args.cmd == "run":
        return run_run(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

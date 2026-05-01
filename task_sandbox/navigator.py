"""Navigator - drives a device to a NavTarget.

Each handler has signature ``(ctx, stage_resolver) -> bool`` and is
responsible for both performing the transition AND verifying arrival
(returning True on success, False on failure). This keeps each handler
self-contained instead of forcing every NavTarget to map to a single
stage_resolver string - useful because the existing CNN stage resolver
only knows a handful of states (主頁面 / 領取 / 放置獎勵 / ...) and
cannot identify pages like "lamp_page".

navigate_to() retries the handler up to max_attempts; before each
non-MAIN_PAGE attempt it routes through the MAIN_PAGE handler so we
always start from a known state.

Tests substitute handlers via monkeypatching NAV_HANDLERS.
"""
from __future__ import annotations

from typing import Callable

from .exceptions import NavigationFailed
from .nav_target import NavTarget
from .spec import TaskContext

StageResolver = Callable[[TaskContext], str]
NavHandler = Callable[[TaskContext, StageResolver], bool]


def _main_page_handler(ctx: TaskContext, stage_resolver: StageResolver) -> bool:
    """Force the device back to main page.

    If already there, return True immediately. Otherwise stop the game
    and re-check; the outer runtime is expected to relaunch the app
    via its existing wakeup flow before the next harness invocation.
    """
    if stage_resolver(ctx) == "主頁面":
        return True
    try:
        ctx.device.app_stop("com.mxdzz.tw.and")
    except Exception:
        pass
    return stage_resolver(ctx) == "主頁面"


NAV_HANDLERS: dict[NavTarget, NavHandler] = {
    NavTarget.MAIN_PAGE: _main_page_handler,
}


def navigate_to(
    ctx: TaskContext,
    target: NavTarget,
    *,
    stage_resolver: StageResolver,
    max_attempts: int = 3,
) -> None:
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        with ctx.recorder.span(f"nav:{target.value}:attempt{attempt}"):
            if target != NavTarget.MAIN_PAGE:
                main_ok = NAV_HANDLERS[NavTarget.MAIN_PAGE](ctx, stage_resolver)
                ctx.recorder.event("nav_step", target="main_page", ok=main_ok)
                if not main_ok:
                    last_error = "main_page recovery failed"
                    continue

            handler = NAV_HANDLERS[target]
            arrived = handler(ctx, stage_resolver)
            ctx.recorder.event("nav_step", target=target.value, ok=arrived)
            if arrived:
                return
            last_error = f"handler returned not arrived for {target.value}"

    raise NavigationFailed(target.value, last_error or "exhausted attempts")

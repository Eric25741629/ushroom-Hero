"""Runtime FSM 的旁路觀察器。

這個 adapter 只保存每台裝置最近的 shadow context、事件數與 mismatch；
不讀取或消費 bot_state 控制訊號，也不執行 ``TransitionDecision.effects``。
因此可以接到 live lifecycle 熱點，卻不改變既有主迴圈行為。
"""
from __future__ import annotations

import copy
import logging
import threading
from dataclasses import dataclass
from typing import Any

from runtime_services.runtime_fsm import (
    ControlMode,
    RuntimeContext,
    RuntimeEvent,
    RuntimePhase,
    ShadowMismatch,
    ShadowObserver,
    TransitionDecision,
)

logger = logging.getLogger(__name__)


@dataclass
class _DeviceShadow:
    context: RuntimeContext
    observer: ShadowObserver
    event_count: int = 0


class RuntimeShadowRegistry:
    """Thread-safe per-device shadow state registry."""

    def __init__(self, logger_obj: Any = logger):
        self._logger = logger_obj
        self._lock = threading.RLock()
        self._devices: dict[str, _DeviceShadow] = {}

    def _get(self, device: str) -> _DeviceShadow:
        state = self._devices.get(device)
        if state is None:
            state = _DeviceShadow(
                context=RuntimeContext(RuntimePhase.SLEEPING, device_id=device),
                observer=ShadowObserver(),
            )
            self._devices[device] = state
        return state

    def observe(
        self,
        device: str,
        event: RuntimeEvent | str,
        observed_phase: RuntimePhase,
        *,
        control_mode: ControlMode | None = None,
    ) -> TransitionDecision:
        """Record one event and the phase observed by the existing runtime.

        The stored context follows the *observed* phase rather than the model's
        prediction. This keeps later observations useful even when a mismatch
        exposes a missing edge in the four-phase pilot.
        """
        if not device:
            raise ValueError("device 不可為空")
        if not isinstance(observed_phase, RuntimePhase):
            raise TypeError("observed_phase 必須是 RuntimePhase")

        with self._lock:
            state = self._get(str(device))
            context = state.context
            if control_mode is not None:
                context = RuntimeContext(
                    phase=context.phase,
                    control_mode=control_mode,
                    device_id=context.device_id,
                )
            before = len(state.observer.mismatches)
            decision = state.observer.observe(context, event, observed_phase)
            state.context = RuntimeContext(
                phase=observed_phase,
                control_mode=context.control_mode,
                device_id=context.device_id,
            )
            state.event_count += 1
            mismatch = (
                state.observer.mismatches[-1]
                if len(state.observer.mismatches) > before
                else None
            )

        if mismatch is not None:
            try:
                self._logger.warning(
                    "[%s] runtime FSM shadow mismatch: event=%s from=%s "
                    "predicted=%s observed=%s reason=%s",
                    device,
                    mismatch.event.value,
                    mismatch.from_phase.value,
                    mismatch.predicted_phase.value,
                    mismatch.observed_phase.value,
                    mismatch.reason,
                )
            except Exception:  # noqa: BLE001 — shadow logging is advisory
                pass
        return decision

    def snapshot(self, device: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._devices.get(str(device))
            if state is None:
                return None
            mismatches = state.observer.mismatches
            return {
                "device": str(device),
                "phase": state.context.phase.value,
                "control_mode": state.context.control_mode.value,
                "event_count": state.event_count,
                "mismatch_count": len(mismatches),
                "last_mismatch": _mismatch_payload(mismatches[-1])
                if mismatches else None,
            }

    def all_snapshots(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            devices = list(self._devices)
        return {
            device: copy.deepcopy(snapshot)
            for device in devices
            if (snapshot := self.snapshot(device)) is not None
        }


def _mismatch_payload(mismatch: ShadowMismatch) -> dict[str, Any]:
    return {
        "event": mismatch.event.value,
        "from_phase": mismatch.from_phase.value,
        "predicted_phase": mismatch.predicted_phase.value,
        "observed_phase": mismatch.observed_phase.value,
        "reason": mismatch.reason,
    }


_REGISTRY = RuntimeShadowRegistry()


def observe_runtime_event(
    device: str,
    event: RuntimeEvent | str,
    observed_phase: RuntimePhase,
    *,
    control_mode: ControlMode | None = None,
) -> TransitionDecision | None:
    """Best-effort live hook; shadow failures never break the device loop."""
    try:
        return _REGISTRY.observe(
            device,
            event,
            observed_phase,
            control_mode=control_mode,
        )
    except Exception:  # noqa: BLE001 — telemetry must never affect runtime
        logger.debug("[%s] runtime FSM shadow observation failed", device,
                     exc_info=True)
        return None


def runtime_shadow_snapshot(device: str) -> dict[str, Any] | None:
    return _REGISTRY.snapshot(device)


def runtime_shadow_snapshots() -> dict[str, dict[str, Any]]:
    return _REGISTRY.all_snapshots()


__all__ = [
    "RuntimeShadowRegistry",
    "observe_runtime_event",
    "runtime_shadow_snapshot",
    "runtime_shadow_snapshots",
]

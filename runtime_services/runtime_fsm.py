"""窄版裝置生命週期 FSM（W13 shadow mode）。

這個模組只描述「若收到一個已分類事件，生命週期應該往哪裡走」；它
不讀取 bot_state、不連 ADB/WS、不開瀏覽器，也不改動現有主迴圈。
實際 live path 仍由 ``new_main_v2`` 決定，本模組可由未來的 shadow adapter
在旁路計算預期結果。

W13 有意只試點四個 phase 與五個 event。暫停是獨立的 control mode，
因此不會產生 ``PAUSED_*`` phase。手動接管在既有決議中是獨立 phase，
但不屬於本輪四 phase 試點；在接線前不得假裝把它塞入這張表。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, runtime_checkable


class RuntimePhase(str, Enum):
    """W13 的四個生命週期 phase。"""

    WS_PHASE = "WS_PHASE"
    WAKING_CLIENT = "WAKING_CLIENT"
    CLIENT_TASKS = "CLIENT_TASKS"
    SLEEPING = "SLEEPING"


class ControlMode(str, Enum):
    """與生命週期 phase 正交的控制狀態。"""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class RuntimeEvent(str, Enum):
    """W13 僅接受的五個事件。"""

    WS_COMPLETED = "WS_COMPLETED"
    CLIENT_READY = "CLIENT_READY"
    TASKS_COMPLETED = "TASKS_COMPLETED"
    WAKE_DUE = "WAKE_DUE"
    FORCE_SLEEP = "FORCE_SLEEP"


class TransitionStatus(str, Enum):
    """轉移結果；拒絕與刻意丟棄不能靜默混在一起。"""

    APPLIED = "APPLIED"
    IGNORED = "IGNORED"
    REJECTED = "REJECTED"


class EffectIntent(str, Enum):
    """由 effect layer 執行的意圖，不在 FSM 內執行副作用。"""

    START_CLIENT = "START_CLIENT"
    RUN_CLIENT_TASKS = "RUN_CLIENT_TASKS"
    SCHEDULE_SLEEP = "SCHEDULE_SLEEP"
    START_WS = "START_WS"
    FORCE_SLEEP = "FORCE_SLEEP"


# ``RuntimeEffect`` 是較直觀的別名，方便呼叫端把它當作 effect type。
RuntimeEffect = EffectIntent


@dataclass(frozen=True)
class RuntimeContext:
    """transition 的最小輸入。

    ``device_id`` 只供 shadow log 辨識，不參與轉移，也不應被拿來放
    任意 runtime dependency。control mode 會原樣帶過每次 phase transition。
    """

    phase: RuntimePhase
    control_mode: ControlMode = ControlMode.RUNNING
    device_id: str | None = None


@dataclass(frozen=True)
class TransitionDecision:
    """純函式轉移的完整結果。

    ``to_context`` 在拒絕或丟棄時等於 ``from_context``，因此 caller 不必
    猜測非法事件是否偷偷改了狀態。``effects`` 只包含 intent，交給外層
    executor 執行。
    """

    event: RuntimeEvent
    from_context: RuntimeContext
    to_context: RuntimeContext
    status: TransitionStatus
    reason: str
    effects: tuple[EffectIntent, ...] = ()
    priority: int = 0

    @property
    def accepted(self) -> bool:
        """是否真的套用了 phase transition。"""

        return self.status is TransitionStatus.APPLIED

    @property
    def ok(self) -> bool:
        """``accepted`` 的簡短唯讀別名，方便 effect layer 寫 guard。"""

        return self.accepted

    @property
    def applied(self) -> bool:
        return self.accepted

    @property
    def rejected(self) -> bool:
        return self.status is TransitionStatus.REJECTED

    @property
    def ignored(self) -> bool:
        return self.status is TransitionStatus.IGNORED

    @property
    def from_phase(self) -> RuntimePhase:
        return self.from_context.phase

    @property
    def to_phase(self) -> RuntimePhase:
        return self.to_context.phase

    @property
    def next_phase(self) -> RuntimePhase:
        """常用的唯讀簡寫；不代表已寫回 live runtime。"""

        return self.to_phase

    @property
    def next_context(self) -> RuntimeContext:
        return self.to_context

    @property
    def phase(self) -> RuntimePhase:
        """預期的下一個 phase；保留 ``to_phase`` 作為明確名稱。"""

        return self.to_phase

    @property
    def effect(self) -> EffectIntent | None:
        """單一 effect 的便利存取；多 effect 時回 ``None``。"""

        return self.effects[0] if len(self.effects) == 1 else None


# 優先級只保留試點內能觀察到的事件。完整候選順序仍是
# SHUTDOWN > FORCE_SLEEP > LOGIN_CONFLICT > MANUAL_LAUNCH > PAUSE > WAKE_OVERRIDE；
# 後五者以外的事件尚未納入 W13，避免悄悄擴大 FSM 的責任。
EVENT_PRIORITY: Mapping[RuntimeEvent, int] = MappingProxyType(
    {
        RuntimeEvent.FORCE_SLEEP: 100,
        RuntimeEvent.WS_COMPLETED: 40,
        RuntimeEvent.CLIENT_READY: 30,
        RuntimeEvent.TASKS_COMPLETED: 20,
        RuntimeEvent.WAKE_DUE: 10,
    }
)


# 每個合法 edge 的唯一真相來源。None 表示事件在該 phase 沒有合法 edge。
_TRANSITIONS: Mapping[tuple[RuntimePhase, RuntimeEvent], tuple[RuntimePhase, EffectIntent, str]] = MappingProxyType(
    {
        (RuntimePhase.WS_PHASE, RuntimeEvent.WS_COMPLETED): (
            RuntimePhase.WAKING_CLIENT,
            EffectIntent.START_CLIENT,
            "WS 階段完成，開始喚醒 client",
        ),
        (RuntimePhase.WAKING_CLIENT, RuntimeEvent.CLIENT_READY): (
            RuntimePhase.CLIENT_TASKS,
            EffectIntent.RUN_CLIENT_TASKS,
            "client 已就緒，開始 client tasks",
        ),
        (RuntimePhase.CLIENT_TASKS, RuntimeEvent.TASKS_COMPLETED): (
            RuntimePhase.SLEEPING,
            EffectIntent.SCHEDULE_SLEEP,
            "client tasks 完成，交由 scheduler 進入休眠",
        ),
        (RuntimePhase.SLEEPING, RuntimeEvent.WAKE_DUE): (
            RuntimePhase.WS_PHASE,
            EffectIntent.START_WS,
            "已達喚醒時間，開始 WS 階段",
        ),
        # FORCE_SLEEP 是唯一能從所有三個 active phase 直接切到睡眠的 edge。
        (RuntimePhase.WS_PHASE, RuntimeEvent.FORCE_SLEEP): (
            RuntimePhase.SLEEPING,
            EffectIntent.FORCE_SLEEP,
            "強制休眠覆蓋 WS 階段",
        ),
        (RuntimePhase.WAKING_CLIENT, RuntimeEvent.FORCE_SLEEP): (
            RuntimePhase.SLEEPING,
            EffectIntent.FORCE_SLEEP,
            "強制休眠覆蓋 client 喚醒",
        ),
        (RuntimePhase.CLIENT_TASKS, RuntimeEvent.FORCE_SLEEP): (
            RuntimePhase.SLEEPING,
            EffectIntent.FORCE_SLEEP,
            "強制休眠覆蓋 client tasks",
        ),
    }
)


def _coerce_event(event: RuntimeEvent | str) -> RuntimeEvent:
    if isinstance(event, RuntimeEvent):
        return event
    try:
        return RuntimeEvent(event)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"未知 RuntimeEvent: {event!r}") from exc


def _decision(
    *,
    event: RuntimeEvent,
    context: RuntimeContext,
    status: TransitionStatus,
    reason: str,
    to_phase: RuntimePhase | None = None,
    effects: tuple[EffectIntent, ...] = (),
) -> TransitionDecision:
    target = context if to_phase is None else RuntimeContext(
        phase=to_phase,
        control_mode=context.control_mode,
        device_id=context.device_id,
    )
    return TransitionDecision(
        event=event,
        from_context=context,
        to_context=target,
        status=status,
        reason=reason,
        effects=effects,
        priority=EVENT_PRIORITY[event],
    )


def transition(context: RuntimeContext, event: RuntimeEvent | str) -> TransitionDecision:
    """計算一個 runtime edge；純函式、零 I/O、零 live state mutation。

    一般 lifecycle event 只能在其來源 phase 消費。非來源 phase 的
    ``WAKE_DUE`` 依既有「醒著時丟棄喚醒覆寫」語意回傳 ``IGNORED``；其餘
    非法 edge 回傳 ``REJECTED``。已在休眠時再次收到 FORCE_SLEEP 是安全
    的 idempotent no-op，不重複產生 sleep effect。
    """

    if not isinstance(context, RuntimeContext):
        raise TypeError("context 必須是 RuntimeContext")
    if not isinstance(context.phase, RuntimePhase):
        raise TypeError("context.phase 必須是 RuntimePhase")
    if not isinstance(context.control_mode, ControlMode):
        raise TypeError("context.control_mode 必須是 ControlMode")

    normalized_event = _coerce_event(event)
    edge = _TRANSITIONS.get((context.phase, normalized_event))
    if edge is not None:
        target, effect, reason = edge
        return _decision(
            event=normalized_event,
            context=context,
            status=TransitionStatus.APPLIED,
            to_phase=target,
            effects=(effect,),
            reason=reason,
        )

    if context.phase is RuntimePhase.SLEEPING and normalized_event is RuntimeEvent.FORCE_SLEEP:
        return _decision(
            event=normalized_event,
            context=context,
            status=TransitionStatus.IGNORED,
            reason="裝置已在休眠中，強制休眠為 idempotent no-op",
        )

    if normalized_event is RuntimeEvent.WAKE_DUE and context.phase is not RuntimePhase.SLEEPING:
        return _decision(
            event=normalized_event,
            context=context,
            status=TransitionStatus.IGNORED,
            reason="裝置已醒著，丟棄無效的喚醒事件",
        )

    return _decision(
        event=normalized_event,
        context=context,
        status=TransitionStatus.REJECTED,
        reason=f"{normalized_event.value} 不允許從 {context.phase.value} 消費",
    )


@runtime_checkable
class EffectExecutor(Protocol):
    """effect layer 的最小合約；實作可連真實服務，但 FSM 不會持有它。"""

    def execute(self, effect: EffectIntent) -> object:
        ...


def execute_effects(decision: TransitionDecision, executor: EffectExecutor | Callable[[EffectIntent], object]) -> tuple[object, ...]:
    """在純 transition 之外執行 effect intents。

    這是刻意獨立的薄層，便於用 fake executor 驗證 FSM 只提出意圖；拒絕
    或丟棄的事件沒有 effect，因而不會誤觸 live 行為。
    """

    if not decision.effects:
        return ()
    execute = executor.execute if hasattr(executor, "execute") else executor
    return tuple(execute(effect) for effect in decision.effects)


@dataclass(frozen=True)
class ShadowMismatch:
    """旁路模型與現有 live 行為的單筆差異。"""

    device_id: str | None
    from_phase: RuntimePhase
    event: RuntimeEvent
    predicted_phase: RuntimePhase
    observed_phase: RuntimePhase
    reason: str


ShadowLog = Callable[[ShadowMismatch], object]


class ShadowObserver:
    """可選的旁路觀察器，不寫回 context，也不執行 effect。

    ``logger`` 是 callback 而非具體 logging 物件，讓測試可以直接收集
    結構化 mismatch；未提供 logger 時仍保留 mismatches 供 reviewer 讀取。
    """

    def __init__(self, logger: ShadowLog | None = None):
        self._logger = logger
        self._mismatches: list[ShadowMismatch] = []

    @property
    def mismatches(self) -> tuple[ShadowMismatch, ...]:
        return tuple(self._mismatches)

    def observe(
        self,
        context: RuntimeContext,
        event: RuntimeEvent | str,
        observed_phase: RuntimePhase,
    ) -> TransitionDecision:
        """計算預期 phase，只有分歧時才發出 shadow log。"""

        decision = transition(context, event)
        if not isinstance(observed_phase, RuntimePhase):
            raise TypeError("observed_phase 必須是 RuntimePhase")
        if observed_phase is decision.to_phase:
            return decision

        mismatch = ShadowMismatch(
            device_id=context.device_id,
            from_phase=context.phase,
            event=decision.event,
            predicted_phase=decision.to_phase,
            observed_phase=observed_phase,
            reason=decision.reason,
        )
        self._mismatches.append(mismatch)
        if self._logger is not None:
            # shadow telemetry 不能反向讓 live loop 失敗；logger 本身壞掉時
            # 仍保留 mismatch，讓 caller 可在下一輪取回。
            try:
                self._logger(mismatch)
            except Exception:
                pass
        return decision


__all__ = [
    "ControlMode",
    "EffectExecutor",
    "EffectIntent",
    "EVENT_PRIORITY",
    "RuntimeContext",
    "RuntimeEffect",
    "RuntimeEvent",
    "RuntimePhase",
    "ShadowMismatch",
    "ShadowObserver",
    "TransitionDecision",
    "TransitionStatus",
    "execute_effects",
    "transition",
]

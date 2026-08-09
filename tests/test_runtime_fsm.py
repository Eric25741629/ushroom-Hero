"""W13 runtime FSM：只驗證純模型與旁路 shadow 行為。"""

from __future__ import annotations

import pytest

from runtime_services.runtime_fsm import (
    ControlMode,
    EffectIntent,
    RuntimeContext,
    RuntimeEvent,
    RuntimePhase,
    ShadowObserver,
    TransitionStatus,
    execute_effects,
    transition,
)


class FakeEffectExecutor:
    """不碰裝置的 effect sink，用來證明 transition 不會自行執行副作用。"""

    def __init__(self):
        self.effects = []

    def execute(self, effect):
        self.effects.append(effect)
        return f"executed:{effect.value}"


@pytest.mark.parametrize(
    ("from_phase", "event", "to_phase", "effect"),
    [
        (RuntimePhase.WS_PHASE, RuntimeEvent.WS_COMPLETED, RuntimePhase.WAKING_CLIENT, EffectIntent.START_CLIENT),
        (RuntimePhase.WAKING_CLIENT, RuntimeEvent.CLIENT_READY, RuntimePhase.CLIENT_TASKS, EffectIntent.RUN_CLIENT_TASKS),
        (RuntimePhase.CLIENT_TASKS, RuntimeEvent.TASKS_COMPLETED, RuntimePhase.SLEEPING, EffectIntent.SCHEDULE_SLEEP),
        (RuntimePhase.SLEEPING, RuntimeEvent.WAKE_DUE, RuntimePhase.WS_PHASE, EffectIntent.START_WS),
        (RuntimePhase.WS_PHASE, RuntimeEvent.FORCE_SLEEP, RuntimePhase.SLEEPING, EffectIntent.FORCE_SLEEP),
        (RuntimePhase.WAKING_CLIENT, RuntimeEvent.FORCE_SLEEP, RuntimePhase.SLEEPING, EffectIntent.FORCE_SLEEP),
        (RuntimePhase.CLIENT_TASKS, RuntimeEvent.FORCE_SLEEP, RuntimePhase.SLEEPING, EffectIntent.FORCE_SLEEP),
    ],
)
def test_legal_transitions_are_table_driven(from_phase, event, to_phase, effect):
    context = RuntimeContext(from_phase, device_id="fake-device")

    decision = transition(context, event)

    assert decision.status is TransitionStatus.APPLIED
    assert decision.accepted
    assert decision.from_phase is from_phase
    assert decision.to_phase is to_phase
    assert decision.effects == (effect,)
    assert decision.from_context is context
    assert decision.to_context.device_id == "fake-device"


@pytest.mark.parametrize(
    ("from_phase", "event"),
    [
        (RuntimePhase.WAKING_CLIENT, RuntimeEvent.WS_COMPLETED),
        (RuntimePhase.CLIENT_TASKS, RuntimeEvent.CLIENT_READY),
        (RuntimePhase.SLEEPING, RuntimeEvent.CLIENT_READY),
        (RuntimePhase.WS_PHASE, RuntimeEvent.TASKS_COMPLETED),
    ],
)
def test_illegal_transitions_are_rejected_without_state_change(from_phase, event):
    context = RuntimeContext(from_phase)

    decision = transition(context, event)

    assert decision.status is TransitionStatus.REJECTED
    assert not decision.accepted
    assert decision.to_context == context
    assert decision.effects == ()


@pytest.mark.parametrize("from_phase", list(RuntimePhase))
def test_wake_due_is_explicitly_dropped_when_not_sleeping(from_phase):
    if from_phase is RuntimePhase.SLEEPING:
        pytest.skip("SLEEPING -> WS_PHASE 是合法喚醒 edge")

    context = RuntimeContext(from_phase)
    decision = transition(context, RuntimeEvent.WAKE_DUE)

    assert decision.status is TransitionStatus.IGNORED
    assert decision.to_phase is from_phase
    assert decision.effects == ()
    assert "丟棄" in decision.reason


def test_force_sleep_while_sleeping_is_idempotent_noop():
    context = RuntimeContext(RuntimePhase.SLEEPING)

    decision = transition(context, RuntimeEvent.FORCE_SLEEP)

    assert decision.status is TransitionStatus.IGNORED
    assert decision.to_context == context
    assert decision.effects == ()


def test_pause_is_orthogonal_and_preserved_across_phase_transition():
    context = RuntimeContext(RuntimePhase.CLIENT_TASKS, ControlMode.PAUSED)

    decision = transition(context, RuntimeEvent.TASKS_COMPLETED)

    assert decision.to_phase is RuntimePhase.SLEEPING
    assert decision.to_context.control_mode is ControlMode.PAUSED
    assert "PAUSED" not in {phase.value for phase in RuntimePhase}


def test_w13_scope_has_exactly_four_phases_and_five_events():
    assert list(RuntimePhase) == [
        RuntimePhase.WS_PHASE,
        RuntimePhase.WAKING_CLIENT,
        RuntimePhase.CLIENT_TASKS,
        RuntimePhase.SLEEPING,
    ]
    assert list(RuntimeEvent) == [
        RuntimeEvent.WS_COMPLETED,
        RuntimeEvent.CLIENT_READY,
        RuntimeEvent.TASKS_COMPLETED,
        RuntimeEvent.WAKE_DUE,
        RuntimeEvent.FORCE_SLEEP,
    ]


def test_transition_only_returns_effect_intents_fake_executor_runs_them_explicitly():
    context = RuntimeContext(RuntimePhase.WS_PHASE)
    fake = FakeEffectExecutor()

    decision = transition(context, RuntimeEvent.WS_COMPLETED)
    assert fake.effects == []

    results = execute_effects(decision, fake)

    assert fake.effects == [EffectIntent.START_CLIENT]
    assert results == ("executed:START_CLIENT",)


def test_shadow_observer_logs_only_a_divergence_and_does_not_change_context():
    logged = []
    observer = ShadowObserver(logged.append)
    context = RuntimeContext(RuntimePhase.WS_PHASE, device_id="fake-device")

    matching = observer.observe(context, RuntimeEvent.WS_COMPLETED, RuntimePhase.WAKING_CLIENT)
    assert matching.to_phase is RuntimePhase.WAKING_CLIENT
    assert logged == []
    assert observer.mismatches == ()

    divergent = observer.observe(context, RuntimeEvent.WS_COMPLETED, RuntimePhase.CLIENT_TASKS)
    assert divergent.to_phase is RuntimePhase.WAKING_CLIENT
    assert len(logged) == 1
    assert logged[0].device_id == "fake-device"
    assert logged[0].predicted_phase is RuntimePhase.WAKING_CLIENT
    assert logged[0].observed_phase is RuntimePhase.CLIENT_TASKS
    assert context.phase is RuntimePhase.WS_PHASE


def test_event_priority_keeps_force_sleep_above_wake_due():
    assert transition(RuntimeContext(RuntimePhase.WS_PHASE), RuntimeEvent.FORCE_SLEEP).priority > transition(
        RuntimeContext(RuntimePhase.SLEEPING), RuntimeEvent.WAKE_DUE
    ).priority


def test_unknown_event_is_rejected_at_boundary():
    with pytest.raises(ValueError):
        transition(RuntimeContext(RuntimePhase.SLEEPING), "SHUTDOWN")

"""開神燈啟動清殘留狀態機（純邏輯）測試。

涵蓋三種啟動狀態的分類優先序，以及 resolver 的動作派發、stall 與迭代上限。
"""
from opengold_v2.lamp_startup import (
    LampEntryState,
    StartupAction,
    StartupResolver,
    classify_entry_state,
)


# ── classify_entry_state ──────────────────────────────────────────────────

def test_classify_sell_all_has_priority_over_everything():
    state = classify_entry_state(
        sell_all=True, comparison=True, blocking_popup=True, main=True
    )
    assert state is LampEntryState.SELL_ALL


def test_classify_comparison_when_no_sell_all():
    state = classify_entry_state(
        sell_all=False, comparison=True, blocking_popup=True, main=True
    )
    assert state is LampEntryState.COMPARISON


def test_classify_blocking_popup_when_only_popup():
    state = classify_entry_state(
        sell_all=False, comparison=False, blocking_popup=True, main=False
    )
    assert state is LampEntryState.BLOCKING_POPUP


def test_classify_main_when_only_main():
    state = classify_entry_state(
        sell_all=False, comparison=False, blocking_popup=False, main=True
    )
    assert state is LampEntryState.MAIN


def test_classify_ready_when_nothing_detected():
    state = classify_entry_state(
        sell_all=False, comparison=False, blocking_popup=False, main=False
    )
    assert state is LampEntryState.READY


# ── StartupResolver action dispatch ───────────────────────────────────────

def test_resolver_ready_returns_done():
    r = StartupResolver()
    assert r.next_action(LampEntryState.READY) is StartupAction.DONE


def test_resolver_sell_all_returns_sell_all():
    r = StartupResolver()
    assert r.next_action(LampEntryState.SELL_ALL) is StartupAction.SELL_ALL


def test_resolver_comparison_returns_process_opened_equip():
    r = StartupResolver()
    assert r.next_action(LampEntryState.COMPARISON) is StartupAction.PROCESS_OPENED_EQUIP


def test_resolver_popup_returns_dismiss():
    r = StartupResolver()
    assert r.next_action(LampEntryState.BLOCKING_POPUP) is StartupAction.DISMISS_POPUP


def test_resolver_main_returns_enter_lamp():
    r = StartupResolver()
    assert r.next_action(LampEntryState.MAIN) is StartupAction.ENTER_LAMP


# ── stall / iteration-cap safety ──────────────────────────────────────────

def test_resolver_aborts_when_same_unresolved_state_repeats():
    """同一個未解決狀態連續出現 max_stall 次 → ABORT（避免無限賣不掉/關不掉）。"""
    r = StartupResolver(max_stall=3)
    assert r.next_action(LampEntryState.SELL_ALL) is StartupAction.SELL_ALL  # 1st
    assert r.next_action(LampEntryState.SELL_ALL) is StartupAction.SELL_ALL  # 2nd
    assert r.next_action(LampEntryState.SELL_ALL) is StartupAction.ABORT     # 3rd identical -> abort


def test_resolver_stall_resets_when_state_changes():
    """狀態有變化代表有進展 → stall 歸零，不應誤判 ABORT。"""
    r = StartupResolver(max_stall=3, max_iters=99)
    seq = [
        LampEntryState.SELL_ALL,
        LampEntryState.SELL_ALL,
        LampEntryState.COMPARISON,   # progress -> reset
        LampEntryState.COMPARISON,
        LampEntryState.READY,
    ]
    actions = [r.next_action(s) for s in seq]
    assert StartupAction.ABORT not in actions
    assert actions[-1] is StartupAction.DONE


def test_resolver_aborts_when_iteration_cap_exceeded():
    """即使狀態一直在變（popup 連環跳），超過 max_iters 仍要 ABORT。"""
    r = StartupResolver(max_iters=4, max_stall=99)
    alternating = [
        LampEntryState.BLOCKING_POPUP,
        LampEntryState.MAIN,
        LampEntryState.BLOCKING_POPUP,
        LampEntryState.MAIN,
        LampEntryState.BLOCKING_POPUP,  # 5th call > max_iters=4
    ]
    actions = [r.next_action(s) for s in alternating]
    assert actions[-1] is StartupAction.ABORT

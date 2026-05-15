"""TDD tests for the lamp opening loop's stability state machine.

Behavior under test:
- One lamp click opens 20 lamps; during the batch animation, EXP popups cover
  the count digits, so OCR commonly returns None.
- The bot must detect when the batch has truly finished by requiring the count
  to be readable AND stable for several consecutive ticks.
- When stable + equipment popup detected → handle popup.
- When stable for a long time + no popup → re-engage auto mode.
- When count is unreadable for too long → re-navigate to lamp page.
"""
import sys

import pytest

# Sibling tests (test_daily_pipeline, test_lamp_scheduler) stub `opengold_v2`
# in sys.modules at collection time. Clear those stubs so we can import the
# real package — pytest collects alphabetically so those modules sort first.
for _k in list(sys.modules):
    if _k == "opengold_v2" or _k.startswith("opengold_v2."):
        del sys.modules[_k]

from opengold_v2.lamp_loop_state import (
    LampLoopAction,
    LampLoopState,
)


# ----- baseline / "still in motion" cases -----

def test_first_observation_only_records_baseline():
    state = LampLoopState()

    action = state.tick(lamp_count=100, has_popup=False)

    assert action == LampLoopAction.WAIT


def test_count_changing_is_treated_as_in_motion():
    state = LampLoopState()
    state.tick(100, has_popup=False)

    action = state.tick(80, has_popup=False)

    assert action == LampLoopAction.WAIT


def test_changing_count_resets_stable_streak():
    state = LampLoopState()
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)  # stable_streak == 1

    state.tick(80, has_popup=False)   # change resets streak

    assert state.stable_streak == 0


# ----- unreadable (OCR-None) cases -----

def test_unreadable_does_not_count_as_stable():
    state = LampLoopState()
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)  # stable_streak == 1

    action = state.tick(None, has_popup=False)

    assert action == LampLoopAction.WAIT


def test_unreadable_resets_stable_streak():
    state = LampLoopState()
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)

    state.tick(None, has_popup=False)

    assert state.stable_streak == 0


def test_unreadable_too_long_triggers_renavigate():
    state = LampLoopState(unreadable_renav_threshold=8)

    for _ in range(7):
        action = state.tick(None, has_popup=False)
        assert action == LampLoopAction.WAIT

    final = state.tick(None, has_popup=False)

    assert final == LampLoopAction.RENAVIGATE


def test_renavigate_resets_unreadable_streak():
    state = LampLoopState(unreadable_renav_threshold=8)
    for _ in range(8):
        state.tick(None, has_popup=False)

    assert state.unreadable_streak == 0


# ----- stable + popup → handle popup -----

def test_stable_threshold_one_does_not_yet_act():
    state = LampLoopState(stable_threshold=2)
    state.tick(100, has_popup=False)

    action = state.tick(100, has_popup=True)

    assert action == LampLoopAction.WAIT


def test_stable_threshold_met_with_popup_handles_popup():
    state = LampLoopState(stable_threshold=2)
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)  # streak == 1, below threshold

    action = state.tick(100, has_popup=True)  # streak == 2, meets threshold

    assert action == LampLoopAction.HANDLE_POPUP


def test_handle_popup_resets_stable_streak():
    state = LampLoopState(stable_threshold=2)
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=True)

    assert state.stable_streak == 0


# ----- stable + no popup → re-engage auto -----

def test_stable_no_popup_below_reengage_threshold_just_waits():
    state = LampLoopState(stable_threshold=2, reengage_after_stable=5)
    state.tick(100, has_popup=False)

    for _ in range(3):
        action = state.tick(100, has_popup=False)

    assert action == LampLoopAction.WAIT


def test_stable_no_popup_at_reengage_threshold_reengages_auto():
    state = LampLoopState(stable_threshold=2, reengage_after_stable=5)
    state.tick(100, has_popup=False)

    last_action = None
    for _ in range(5):
        last_action = state.tick(100, has_popup=False)

    assert last_action == LampLoopAction.REENGAGE_AUTO


def test_reengage_auto_resets_stable_streak():
    state = LampLoopState(stable_threshold=2, reengage_after_stable=5)
    state.tick(100, has_popup=False)
    for _ in range(5):
        state.tick(100, has_popup=False)

    assert state.stable_streak == 0


# ----- popup detection only counts AFTER stability is confirmed -----

def test_popup_signal_during_motion_is_ignored():
    """Popup overlay during animation is unreliable; only honor it once stable."""
    state = LampLoopState(stable_threshold=2)

    action = state.tick(100, has_popup=True)  # first read, no baseline yet

    assert action == LampLoopAction.WAIT


# ----- 被動生成（count 上升）不應被當作「動畫中」 -----

def test_count_decrease_is_treated_as_in_motion():
    """開神燈動畫會讓 count 快速下降。"""
    state = LampLoopState()
    state.tick(100, has_popup=False)

    action = state.tick(80, has_popup=False)

    assert action == LampLoopAction.WAIT


def test_count_decrease_resets_stable_streak():
    state = LampLoopState()
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)  # streak == 1

    state.tick(80, has_popup=False)

    assert state.stable_streak == 0


def test_passive_increase_does_not_reset_stable_streak():
    """魔法熔爐被動產生神燈會讓 count 上升；不應視為動畫中。"""
    state = LampLoopState(stable_threshold=2)
    state.tick(100, has_popup=False)
    state.tick(100, has_popup=False)  # streak == 1, below threshold

    state.tick(127, has_popup=False)  # 被動 +27，仍視為穩定累計

    assert state.stable_streak >= 1


def test_passive_increase_can_lead_to_reengage():
    """被動上升不會卡住 reengage 流程。"""
    state = LampLoopState(stable_threshold=2, reengage_after_stable=5)
    state.tick(100, has_popup=False)

    # 中間穿插一次被動 +5；應在累計 5 次「非下降」後觸發 reengage
    actions = [
        state.tick(v, has_popup=False)
        for v in [100, 100, 105, 105, 105]
    ]

    assert actions[-1] == LampLoopAction.REENGAGE_AUTO


# ----- popup detected but count unreadable (popup blocks count ROI) -----
#
# Validated on 7fe98fc6: when the equipment-comparison popup is shown,
# the gold count ROI (y=802-821) is covered by the 出售/裝備 buttons, so
# OCR returns None. The popup itself is visible (skill text reads "暴擊" /
# "回復" etc.). Without this branch tick() loops WAIT → RENAVIGATE forever
# while the popup waits for action.

def test_popup_with_unreadable_count_fires_handle_popup_after_threshold():
    state = LampLoopState(stable_threshold=2)

    state.tick(None, has_popup=True)  # unreadable_streak == 1, below threshold
    action = state.tick(None, has_popup=True)  # unreadable_streak == 2

    assert action == LampLoopAction.HANDLE_POPUP


def test_popup_with_unreadable_count_below_threshold_just_waits():
    state = LampLoopState(stable_threshold=2)

    action = state.tick(None, has_popup=True)

    assert action == LampLoopAction.WAIT


def test_handle_popup_via_unreadable_resets_unreadable_streak():
    state = LampLoopState(stable_threshold=2)

    state.tick(None, has_popup=True)
    state.tick(None, has_popup=True)  # → HANDLE_POPUP

    assert state.unreadable_streak == 0


def test_unreadable_without_popup_still_renavigates():
    """Without a popup signal, prolonged unreadable count must still escalate
    to RENAVIGATE — that's the existing escape hatch for being on the wrong page."""
    state = LampLoopState(stable_threshold=2, unreadable_renav_threshold=8)

    last = None
    for _ in range(8):
        last = state.tick(None, has_popup=False)

    assert last == LampLoopAction.RENAVIGATE


def test_popup_short_circuits_before_unreadable_renavigate():
    """When popup is detected, HANDLE_POPUP must fire BEFORE unreadable_renav
    threshold (which is much higher). Otherwise the bot renavigates away from
    a popup it could have processed."""
    state = LampLoopState(stable_threshold=2, unreadable_renav_threshold=20)

    state.tick(None, has_popup=True)
    action = state.tick(None, has_popup=True)

    assert action == LampLoopAction.HANDLE_POPUP

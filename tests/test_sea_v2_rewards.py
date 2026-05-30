"""sea_v2.rewards — season daily reward-collection (backend-agnostic).

Coordinates were mapped + live-verified on H5 (7fe98fc6, 2026-05-29); these tests pin the
*branching* (view-opened guard, popup-dismiss loop, achievement claim loop, milestone
sweep) against a fake device + stubbed OCR so the logic is safe without a live game.
"""
import sys
import types

import pytest

from sea_v2 import rewards as R


class FakeDevice:
    def __init__(self):
        self.clicks = []

    def click(self, x, y, *a, **k):
        self.clicks.append((x, y))

    def press(self, key, *a, **k):
        self.clicks.append(("press", key))

    def screenshot(self, *a, **k):
        return object()  # opaque; OCR is stubbed so content is irrelevant


@pytest.fixture
def env(monkeypatch):
    """Inject fake img_tools + pause_guard; drive OCR/label-tap from queues.

    state['texts']: queue of OCR-result lists, one popped per get_all_text() call ([] when
    exhausted). state['click_str']: queue of bools returned by click_str_by_server().
    """
    state = {"texts": [], "click_str": [], "click_str_calls": []}

    fake_it = types.ModuleType("img_tools")
    def get_all_text(img, **k):
        q = state["texts"]
        return q.pop(0) if q else []
    def click_str_by_server(d, target, **k):
        state["click_str_calls"].append(target)
        q = state["click_str"]
        return q.pop(0) if q else False
    fake_it.get_all_text = get_all_text
    fake_it.click_str_by_server = click_str_by_server
    monkeypatch.setitem(sys.modules, "img_tools", fake_it)

    fake_pg = types.ModuleType("utils.pause_guard")
    class TaskAborted(Exception):
        pass
    fake_pg.TaskAborted = TaskAborted
    fake_pg.check = lambda: None
    monkeypatch.setitem(sys.modules, "utils.pause_guard", fake_pg)
    import utils
    monkeypatch.setattr(utils, "pause_guard", fake_pg, raising=False)

    # _ocr_find locates variable-position 領取 buttons (任務 tab). Drive from a queue;
    # default empty -> returns None (no daily-task claim), so 目標-only tests are unaffected.
    state["ocr_find"] = []
    monkeypatch.setattr(R, "_ocr_find",
                        lambda d, target, exclude=None: state["ocr_find"].pop(0) if state["ocr_find"] else None)

    monkeypatch.setattr(R, "_SETTLE", 0)
    monkeypatch.setattr(R.time, "sleep", lambda *_: None)
    return state


# ----- _dismiss_reward_popup -----------------------------------------------------

def test_dismiss_popup_taps_blank_until_gone(env):
    env["texts"] = [["恭喜獲得", "點擊空白處關閉"], ["恭喜獲得"], []]  # popup twice then gone
    d = FakeDevice()
    assert R._dismiss_reward_popup(d) is True
    assert d.clicks.count(R.BLANK_TAP) == 2

def test_dismiss_popup_ignores_reward_word_in_view_body(env):
    # 「獎勵」alone is NOT a popup (成就 body reads 達成獎勵 / 累計…戰功) -> must not loop-tap
    env["texts"] = [["達成獎勵", "累計獲得125000戰功"]]
    d = FakeDevice()
    assert R._dismiss_reward_popup(d) is False
    assert R.BLANK_TAP not in d.clicks


# ----- collect_map_income --------------------------------------------------------

def test_map_income_opens_claims_closes(env):
    env["texts"] = [["地圖收益", "儲存時間"], ["恭喜獲得"], []]  # view; close-dismiss popup; gone
    d = FakeDevice()
    assert R.collect_map_income(d) is True
    assert R.BTN_OUTLINE in d.clicks
    assert R.TAB_MAP_INCOME in d.clicks
    assert R.MAP_INCOME_CLAIM in d.clicks       # 地圖收益 領取 = (270,712)
    assert R.OUTLINE_CLOSE in d.clicks

def test_map_income_bails_when_view_absent(env):
    env["texts"] = [["主頁面"]]  # outline view never opened
    d = FakeDevice()
    assert R.collect_map_income(d) is False
    assert R.MAP_INCOME_CLAIM not in d.clicks  # must not blind-tap the claim


def test_dock_supply_claims_correct_button(env):
    # 碼頭補給 領取 is the upper btnGet (270,392), NOT the (270,712) 一鍵補給 slot
    env["texts"] = [["碼頭補給", "發放記錄"], ["恭喜獲得", "點擊空白處關閉"], []]
    d = FakeDevice()
    assert R.collect_dock_supply(d) is True
    assert R.TAB_DOCK_SUPPLY in d.clicks
    assert R.DOCK_SUPPLY_CLAIM in d.clicks      # (270,392)
    assert R.MAP_INCOME_CLAIM not in d.clicks   # must NOT tap the 一鍵補給 slot (270,712)
    assert R.OUTLINE_CLOSE in d.clicks

def test_dock_supply_bails_when_view_absent(env):
    env["texts"] = [["主頁面"]]
    d = FakeDevice()
    assert R.collect_dock_supply(d) is False
    assert R.DOCK_SUPPLY_CLAIM not in d.clicks


# ----- claim_achievements (= 戰功獎勵, fixed-coord top loop, NOT click_str) --------

def test_achievements_claims_top_until_no_more(env):
    # open; iter sees 領取 -> claim (恭喜獲得); again; then no 領取 -> exit
    env["texts"] = [["成就"], ["領取"], ["恭喜獲得"], [], ["領取"], ["恭喜獲得"], [], []]
    d = FakeDevice()
    assert R.claim_achievements(d) == 2
    top = (R.ACHIEVE_GET_X, R.ACHIEVE_GET_YS[0])
    assert top in d.clicks                       # claims via fixed TOP coord (list reflows up)
    assert env["click_str_calls"] == []          # must NOT use fragile OCR click_str
    assert R.ACHIEVE_CLOSE in d.clicks

def test_achievements_exits_without_tapping_when_nothing_claimable(env):
    # only 已領取 on screen -> NO claimable 領取 -> exit WITHOUT a blind claim tap (the bug)
    env["texts"] = [["戰功獎勵"], ["已領取", "達成獎勵", "累計獲得125000戰功"]]
    d = FakeDevice()
    assert R.claim_achievements(d) == 0
    assert (R.ACHIEVE_GET_X, R.ACHIEVE_GET_YS[0]) not in d.clicks  # never blind-tapped
    assert R.ACHIEVE_CLOSE in d.clicks                            # still closed cleanly

def test_achievements_bails_when_view_absent(env):
    env["texts"] = [["主頁面"]]
    d = FakeDevice()
    assert R.claim_achievements(d) == 0
    assert (R.ACHIEVE_GET_X, R.ACHIEVE_GET_YS[0]) not in d.clicks  # no blind claim tap


# ----- claim_target_milestones ---------------------------------------------------

def test_task_panel_claims_daily_task_rewards(env, monkeypatch):
    # 任務 tab: two completed tasks each expose a 領取 (located by OCR); claim both,
    # then no 目標 reds -> done. Verifies the previously-missing 任務-tab claiming.
    monkeypatch.setattr(R, "_red_milestone_nodes", lambda d: [])
    env["ocr_find"] = [(421, 321), (421, 441), None]
    env["texts"] = [["任務", "目標"],
                    ["恭喜獲得"], [],     # claim1 popup+clear
                    ["恭喜獲得"], []]     # claim2 popup+clear
    d = FakeDevice()
    assert R.claim_task_panel(d) == 2
    assert (421, 321) in d.clicks and (421, 441) in d.clicks
    assert R.TAB_REN in d.clicks         # switched to the 任務 tab
    assert R.TASK_CLOSE in d.clicks


def test_target_milestone_claims_each_red_node(env, monkeypatch):
    # two RED milestone nodes; tap each (switch) -> 領取 -> claim; then no reds -> done
    rounds = [[(304, 309), (398, 309)], [(398, 309)], []]
    monkeypatch.setattr(R, "_red_milestone_nodes", lambda d: rounds.pop(0) if rounds else [])
    env["texts"] = [["任務", "懸賞"],
                    ["領取"], ["恭喜獲得"], [],     # round1: switch sees 領取, claim popup+clear
                    ["領取"], ["恭喜獲得"], []]     # round2
    d = FakeDevice()
    assert R.claim_target_milestones(d) == 2
    assert (304, 309) in d.clicks and (398, 309) in d.clicks   # switched to BOTH red nodes
    assert R.GOAL_CLAIM in d.clicks
    assert R.TASK_CLOSE in d.clicks

def test_target_milestone_none_when_no_red(env, monkeypatch):
    monkeypatch.setattr(R, "_red_milestone_nodes", lambda d: [])
    env["texts"] = [["任務"]]
    d = FakeDevice()
    assert R.claim_target_milestones(d) == 0
    assert R.TASK_CLOSE in d.clicks

def test_target_milestone_skips_locked_red_node(env, monkeypatch):
    # a red node that yields no 領取 (locked tier) must be tried once then skipped (seen),
    # not re-tapped forever -> claimed 0, loop terminates, view closed
    monkeypatch.setattr(R, "_red_milestone_nodes", lambda d: [(491, 309)])  # same red every call
    env["texts"] = [["任務"], []]   # open; switch -> no 領取
    d = FakeDevice()
    assert R.claim_target_milestones(d) == 0
    assert d.clicks.count((491, 309)) == 1            # tapped once, then skipped via `seen`
    assert R.TASK_CLOSE in d.clicks


# ----- collect_all_rewards -------------------------------------------------------

def test_collect_all_isolates_step_failure(env, monkeypatch):
    monkeypatch.setattr(R, "collect_map_income", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(R, "collect_dock_supply", lambda d: True)
    monkeypatch.setattr(R, "claim_achievements", lambda d: 3)
    monkeypatch.setattr(R, "claim_target_milestones", lambda d: 2)
    r = R.collect_all_rewards(FakeDevice())
    assert r.map_income is False        # the throwing step is swallowed
    assert r.dock_supply is True
    assert r.achievements == 3
    assert r.target_milestones == 2

def test_collect_all_propagates_task_aborted(env, monkeypatch):
    from utils import pause_guard
    monkeypatch.setattr(R, "collect_map_income",
                        lambda d: (_ for _ in ()).throw(pause_guard.TaskAborted("paused")))
    with pytest.raises(pause_guard.TaskAborted):
        R.collect_all_rewards(FakeDevice())

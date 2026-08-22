"""web_h5 寶箱/離線收益的無 OCR 決策測試。"""

import sys
import types


# reward_manager keeps the ADB/OCR legacy function in the same module. Stub
# those optional imports so these tests exercise only the Cocos decision layer.
if "img_tools" not in sys.modules:
    _img_tools = types.ModuleType("img_tools")
    _img_tools.wait_for_any_text = lambda *args, **kwargs: None
    sys.modules["img_tools"] = _img_tools
if "tools" not in sys.modules:
    _tools = types.ModuleType("tools")
    _tools.click_white = lambda *args, **kwargs: None
    sys.modules["tools"] = _tools

import game_actions.reward_manager as reward_manager  # noqa: E402


def test_quick_2h_zero_of_three_is_exhausted():
    state = {
        "active": True,
        "clickable": True,
        "labels": ["2小時收益", "(0/3)", "29:09"],
    }

    assert reward_manager._parse_quick_remaining(state["labels"]) == 0
    assert reward_manager._quick_2h_decision(state) == (
        "skip",
        "daily_quota_exhausted",
    )


def test_quick_2h_cooldown_is_skipped_without_clicking():
    state = {
        "active": True,
        "clickable": True,
        "labels": ["2小時收益", "(2/3)", "29:09"],
    }

    assert reward_manager._parse_quick_cooldown(state["labels"]) == 29 * 60 + 9
    assert reward_manager._quick_2h_decision(state) == (
        "skip",
        "cooldown_1749s",
    )


def test_quick_2h_is_eligible_when_counter_and_cooldown_allow_it():
    state = {"active": True, "clickable": True, "labels": ["(1/3)", "00:00"]}

    assert reward_manager._quick_2h_decision(state) == ("claim", "eligible")


def test_live_paths_match_the_verified_cocos_scene():
    assert reward_manager._HOME_IDLE_CHEST_PATH[-3:] == (
        "subRoots", "boxRoot", "btnBox"
    )
    assert reward_manager._OFFLINE_REWARD_QUICK_2H_PATH[-1] == "btnAd"
    assert reward_manager._OFFLINE_REWARD_BASE_PATH[-1] == "btnStart"


def test_web_idle_reward_composes_chest_quick_and_base_claim(monkeypatch):
    events = []
    view_state = {"outlinePopView": False, "GoodsGetView": False}

    def fake_view(_page, names):
        return any(view_state.get(name, False) for name in names)

    def fake_node(_page, path, action):
        events.append((path[-1], action))
        if path == reward_manager._HOME_IDLE_CHEST_PATH:
            view_state["outlinePopView"] = True
        return {"ok": True}

    monkeypatch.setattr(reward_manager, "_view_active", fake_view)
    monkeypatch.setattr(reward_manager, "_cocos_node_action", fake_node)
    monkeypatch.setattr(reward_manager, "_wait_for_view", lambda *a, **k: True)
    monkeypatch.setattr(reward_manager, "_claim_quick_2h", lambda _page: "skipped")
    monkeypatch.setattr(
        reward_manager, "_claim_base_reward", lambda _page: events.append("base") or True
    )

    report = reward_manager.run_web_idle_reward(object())

    assert report == {
        "opened": True,
        "quick_2h": "skipped",
        "base": True,
        "success": True,
    }
    assert events == [("btnBox", "click"), "base"]

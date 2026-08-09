from unittest.mock import MagicMock, patch
import sys
from types import SimpleNamespace

sys.modules.setdefault("img_tools", SimpleNamespace(
    click_str_by_server=MagicMock(), wait_for_any_text=MagicMock()
))

from game_actions import arena_battle
from game_actions.cocos_arena import CocosArena


def test_arena_enter_uses_cocos_text_and_verifies_result_list():
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()
    arena.ui.click_text.return_value = True
    arena.ui.wait_for_text.side_effect = ["挑戰", "刷新"]

    assert arena.enter() is True
    assert arena.ui.click_text.call_args_list[0].args == ("競技場",)


def test_web_h5_animation_fight_does_not_call_ocr():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    cocos = MagicMock()
    cocos.challenge.return_value = True
    cocos.wait_result.return_value = "勝利"
    with patch.object(arena_battle, "_cocos_arena", return_value=cocos), \
         patch.object(arena_battle.img_tools, "click_str_by_server") as click_ocr, \
         patch.object(arena_battle.img_tools, "wait_for_any_text", create=True) as wait_ocr, \
         patch.object(arena_battle, "enforce_gap", return_value=0):
        arena_battle._run_animation_fights(d, "web", 1, 0)
    click_ocr.assert_not_called()
    wait_ocr.assert_not_called()


def test_cocos_finish_closes_overlay_and_never_clicks_battle_record():
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()

    with patch("utils.cocos_navigator.CocosNavigator") as navigator_cls:
        navigator = navigator_cls.return_value
        navigator.goto_main.return_value = True

        assert arena.finish() is True

    navigator.dismiss_blocking_popups.assert_called_once_with()
    navigator.goto_main.assert_called_once_with()
    assert not any(
        call.args and call.args[0] == "記錄"
        for call in arena.ui.click_text.call_args_list
    )


def test_cocos_enter_failure_keeps_ocr_finish_path(monkeypatch):
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    cocos = MagicMock()
    cocos.enter.return_value = False

    monkeypatch.setattr(
        "config_manager.get_device_config_dict",
        lambda _ip: {"arena_battle_mode": "animation", "arena_fight_gap_sec": 7},
    )
    with patch.object(arena_battle, "_cocos_arena", return_value=cocos) as make_cocos, \
         patch.object(arena_battle.img_tools, "click_str_by_server", return_value=True) as click_ocr, \
         patch.object(arena_battle.img_tools, "wait_for_any_text", return_value="勝利"), \
         patch.object(arena_battle, "enforce_gap", return_value=0), \
         patch.object(arena_battle.time, "sleep"), \
         patch("utils.cocos_navigator.CocosNavigator") as navigator_cls:
        navigator_cls.return_value.current_view.return_value = "main"

        assert arena_battle.run_arena_challenges(d, "web-5558") is True

    # Cocos enter failed: the three fights and finish must stay on the OCR path.
    assert make_cocos.call_count == 1
    cocos.finish.assert_not_called()
    assert [call.args[1] for call in click_ocr.call_args_list[-2:]] == ["刷新", "記錄"]

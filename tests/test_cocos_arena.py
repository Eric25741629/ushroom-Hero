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

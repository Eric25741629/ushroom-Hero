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
    assert arena.ui.click_text.call_args_list[1].kwargs["occurrence"] == 0


def test_arena_challenge_accepts_failure_result_from_cdp():
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()
    arena.ui.click_text.return_value = True
    arena.ui.wait_for_text.return_value = "失敗"

    assert arena.challenge() is True
    assert "跳過" in arena.ui.wait_for_text.call_args.args[0]
    assert "战斗胜利" in arena.ui.wait_for_text.call_args.args[0]


def test_arena_wait_result_normalises_simplified_battle_result():
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()
    arena.ui.wait_for_text.return_value = "战斗胜利"

    assert arena.wait_result(timeout=3) == "勝利"


def test_arena_wait_result_skips_simplified_skip_button():
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()
    arena.ui.wait_for_text.side_effect = ["跳过", "战斗失败"]

    assert arena.wait_result(timeout=3) == "失敗"
    arena.ui.click_text.assert_called_once_with("跳过")


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


def test_web_h5_animation_failure_popup_finishes_without_ocr():
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    cocos = MagicMock()
    cocos.challenge.return_value = True
    cocos.wait_result.return_value = "失敗"
    with patch.object(arena_battle, "_cocos_arena", return_value=cocos), \
         patch.object(arena_battle.img_tools, "click_str_by_server") as click_ocr, \
         patch.object(arena_battle.img_tools, "wait_for_any_text", create=True) as wait_ocr, \
         patch.object(arena_battle, "enforce_gap", return_value=0):
        assert arena_battle._run_animation_fights(d, "7fe98fc6", 1, 0) is True
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


def test_cocos_finish_closes_result_mask_before_navigating_home(monkeypatch):
    arena = CocosArena(MagicMock())
    arena.ui = MagicMock()

    with patch("utils.cocos_navigator.CocosNavigator") as navigator_cls:
        navigator = navigator_cls.return_value
        navigator.current_view.return_value = "unknown"
        navigator.goto_main.return_value = True
        monkeypatch.setattr("game_actions.cocos_arena.time.sleep", lambda *_: None)

        assert arena.finish() is True

    navigator._click_path.assert_called_once_with(
        "/UIRoot/NormalView/PvpResultView/imgMask"
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


def test_pure_ws_config_uses_h5_animation_without_second_ws(monkeypatch):
    d = MagicMock(backend_kind="web_h5", _page=MagicMock())
    cocos = MagicMock()
    cocos.enter.return_value = True
    cocos.challenge.return_value = True
    cocos.wait_result.return_value = "勝利"
    cocos.finish.return_value = True

    monkeypatch.setattr(
        "config_manager.get_device_config_dict",
        lambda _ip: {
            "arena_battle_mode": "pure_ws",
            "arena_fight_gap_sec": 7,
            "arena_daily_fights": 1,
        },
    )
    with patch.object(
        arena_battle,
        "_run_pure_ws_fights",
        side_effect=AssertionError("H5 arena must not open a second WS"),
    ) as pure_ws, \
         patch.object(arena_battle, "_cocos_arena", return_value=cocos), \
         patch.object(arena_battle.img_tools, "click_str_by_server") as click_ocr, \
         patch.object(arena_battle.img_tools, "wait_for_any_text", create=True) as wait_ocr, \
         patch.object(arena_battle, "enforce_gap", return_value=0):
        assert arena_battle.run_arena_challenges(d, "7fe98fc6") is True

    pure_ws.assert_not_called()
    cocos.enter.assert_called_once_with()
    cocos.challenge.assert_called_once_with()
    cocos.wait_result.assert_called_once()
    cocos.finish.assert_called_once_with()
    click_ocr.assert_not_called()
    wait_ocr.assert_not_called()


def test_pure_ws_arena_already_at_target_does_not_login(monkeypatch):
    from ws_token import arena_fight
    from ws_token import creds

    monkeypatch.setattr(arena_fight, "daily_fight_plan", lambda _ip, _n: (9, 0))
    monkeypatch.setattr(
        creds,
        "load_creds",
        lambda _ip: (_ for _ in ()).throw(AssertionError("已達標不應登入")),
    )

    assert arena_battle._run_pure_ws_fights("dev", 9, 7, {}) is True

"""Offline tests for the H5/CDP mining executor.

These tests use a fake Playwright page. They must never attach to live CDP or
consume mining items.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token.mining import GOODS_BOMB, GOODS_DRILL, GOODS_PICKAXE  # noqa: E402
from ws_token.mining_h5_executor import H5MiningExecutor  # noqa: E402
from utils.web_game_api import _build_dig_request_body  # noqa: E402


class FakePage:
    def __init__(self):
        self.calls = []

    def evaluate(self, script, arg=None):
        self.calls.append((script, arg))
        if "expectCmds" in script:
            return {"cmd": 0x0C03, "body": []}
        return {"ok": True}


def test_refresh_board_calls_h5_req_home_info_after_cooldown_reset():
    page = FakePage()
    result = H5MiningExecutor(page).refresh_board()

    assert result == {"ok": True}
    script, arg = page.calls[-1]
    assert arg is None
    assert "MysteryDataCache" in script
    assert "lastReqTime = 0" in script
    assert "MysteryControl" in script
    assert "reqHomeInfo()" in script


def test_use_pickaxe_sends_goods_id_and_block_id_to_h5():
    page = FakePage()
    result = H5MiningExecutor(page).use_pickaxe(16238803)

    assert result["ok"] is True
    assert result["response_cmd"] == 0x0C03
    script, arg = page.calls[-1]
    assert "expectCmds" in script
    assert arg[0] == 0x0C03
    assert arg[1] == list(_build_dig_request_body(GOODS_PICKAXE, 16238803))
    assert arg[-1] == [0x0C03, 0x0201]


def test_use_bomb_sends_goods_id_and_block_id_to_h5():
    page = FakePage()
    result = H5MiningExecutor(page).use_bomb(16238804)

    assert result["ok"] is True
    assert result["response_cmd"] == 0x0C03
    script, arg = page.calls[-1]
    assert "expectCmds" in script
    assert arg[0] == 0x0C03
    assert arg[1] == list(_build_dig_request_body(GOODS_BOMB, 16238804))


def test_use_drill_sends_goods_id_and_block_id_to_h5():
    page = FakePage()
    result = H5MiningExecutor(page).use_drill(16238804)

    assert result["ok"] is True
    assert result["response_cmd"] == 0x0C03
    script, arg = page.calls[-1]
    assert "expectCmds" in script
    assert arg[0] == 0x0C03
    assert arg[1] == list(_build_dig_request_body(GOODS_DRILL, 16238804))


def test_use_goods_decodes_server_error_response():
    class ErrorPage(FakePage):
        def evaluate(self, script, arg=None):
            self.calls.append((script, arg))
            if "expectCmds" in script:
                return {"cmd": 0x0201, "body": [0x08, 0x47]}
            return {"ok": True}

    result = H5MiningExecutor(ErrorPage()).use_pickaxe(16238803)

    assert result == {
        "ok": False,
        "response_cmd": 0x0201,
        "error_code": 71,
        "raw_body_hex": "0847",
    }

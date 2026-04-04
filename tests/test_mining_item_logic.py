import sys
import types

def _noop(*_args, **_kwargs):
    return None

if "uiautomator2" not in sys.modules:
    sys.modules["uiautomator2"] = types.SimpleNamespace(Device=object)
if "img_tools" not in sys.modules:
    sys.modules["img_tools"] = types.SimpleNamespace(get_all_text=lambda _frame: [])
if "tools" not in sys.modules:
    sys.modules["tools"] = types.SimpleNamespace(click_white=lambda _device: None)
if "miner.models.classifier" not in sys.modules:
    sys.modules["miner.models.classifier"] = types.SimpleNamespace(
        ClassifierCNN=object,
        load_cnn_model=lambda: (None, None, None),
    )
if "miner.planning.executor" not in sys.modules:
    sys.modules["miner.planning.executor"] = types.SimpleNamespace(
        execute_plan_steps=lambda *_args, **_kwargs: None,
    )
if "miner.core.ocr_utils" not in sys.modules:
    sys.modules["miner.core.ocr_utils"] = types.SimpleNamespace(
        check_pickaxe_count=lambda *_args, **_kwargs: 20,
        check_drill_num=lambda *_args, **_kwargs: 0,
        check_boom_num=lambda *_args, **_kwargs: 0,
    )
if "miner.planning.smart_planner" not in sys.modules:
    sys.modules["miner.planning.smart_planner"] = types.SimpleNamespace(
        plan_smart=lambda *_args, **_kwargs: {"ok": False, "steps": []},
    )
if "miner.rl.rl_recorder" not in sys.modules:
    sys.modules["miner.rl.rl_recorder"] = types.SimpleNamespace(RLRecorder=object)
if "miner.core.vision_utils" not in sys.modules:
    sys.modules["miner.core.vision_utils"] = types.SimpleNamespace(
        check_points=lambda *_args, **_kwargs: (False, None),
    )
if "utils.logging_utils" not in sys.modules:
    sys.modules["utils.logging_utils"] = types.SimpleNamespace(
        logger=None,
        setup_miner_logger=lambda _ip: types.SimpleNamespace(
            info=_noop,
            debug=_noop,
            warning=_noop,
            error=_noop,
        ),
    )
if "config.paths" not in sys.modules:
    sys.modules["config.paths"] = types.SimpleNamespace(DATASET_LOW_CONFIDENCE_DIR_STR="")

from miner.mining_service import _dismiss_mining_overlay_if_needed
from miner.planning.item_planner import find_tool_candidate, plan_with_items_ev


class _DummyDevice:
    pass


def test_item_planner_prefers_boundary_bomb_for_partial_square():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[3][5] = "reachable_pit"
    board[4][5] = "reachable_pit"

    plan = plan_with_items_ev(board, {"drill": 0, "bomb": 1})

    assert plan["ok"] is True
    assert plan["steps"][0]["action"] == "use_bomb"


def test_find_tool_candidate_prefers_bottom_triplet_bomb_with_hidden_gain():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[6][1] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"

    candidate = find_tool_candidate(board, {"drill": 0, "bomb": 1})

    assert candidate is not None
    assert candidate["tool"] == "bomb"
    assert candidate["inferred_gain"] >= 9


def test_dismiss_mining_overlay_when_ocr_finds_mine_text(monkeypatch):
    clicked = {"value": False}

    def fake_get_all_text(_frame):
        return ["發現礦洞"]

    def fake_click_white(_device):
        clicked["value"] = True

    class _Logger:
        def info(self, _msg):
            return None

        def debug(self, _msg):
            return None

    monkeypatch.setattr("miner.mining_service.img_tools.get_all_text", fake_get_all_text)
    monkeypatch.setattr("miner.mining_service.click_white", fake_click_white)

    handled = _dismiss_mining_overlay_if_needed(_DummyDevice(), object(), _Logger())

    assert handled is True
    assert clicked["value"] is True



def test_top_row_pit_must_be_prioritized():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[0][0] = "reachable_pit"
    board[6][1] = "reachable_pit"
    board[6][2] = "reachable_pit"
    board[6][3] = "reachable_pit"

    candidate = find_tool_candidate(board, {"drill": 1, "bomb": 1})

    assert candidate is not None
    assert (0, 0) in candidate["pits"]


def test_partial_2x2_cluster_should_not_count_as_full_gain():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[2][2] = "reachable_pit"
    board[2][3] = "reachable_pit"
    board[3][2] = "reachable_pit"
    board[3][3] = "reachable_pit"

    candidate = find_tool_candidate(board, {"drill": 1, "bomb": 0})

    assert candidate is None



def test_unreachable_empty_should_not_be_used_as_item_target():
    board = [["empty" for _ in range(6)] for _ in range(7)]
    board[6][2] = "unreachable_empty"
    board[6][3] = "reachable_pit"

    candidate = find_tool_candidate(board, {"drill": 0, "bomb": 1})

    assert candidate is None

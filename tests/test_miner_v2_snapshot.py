from miner.v2.types import BoardSnapshot, ScreenCheckResult
from miner.v2.visualization import format_board


def test_board_snapshot_metrics():
    snapshot = BoardSnapshot(
        board=[["empty", "dirt"], ["reachable_pit", "rock"]],
        confidences=[[0.9, 0.7], [0.8, 0.4]],
        captured_at="2026-04-09T23:30:00",
        image_shape=(100, 200, 3),
        grid_config={"H": 2, "W": 2},
        screen_check=ScreenCheckResult(passed=True, matched_points=7),
    )

    assert snapshot.rows == 2
    assert snapshot.cols == 2
    assert snapshot.min_confidence == 0.4
    assert abs(snapshot.avg_confidence - 0.7) < 1e-9
    assert snapshot.screen_check is not None
    assert snapshot.screen_check.matched_points == 7


def test_format_board_renders_expected_symbols():
    rendered = format_board(
        [
            ["empty", "dirt", "reachable_pit"],
            ["unreachable_empty", "unreachable_rock", "unreachable_pit"],
        ]
    )

    assert "0 1 2" in rendered
    assert " 0 . D *" in rendered
    assert " 1 _ r X" in rendered

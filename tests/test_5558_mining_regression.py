from unittest.mock import MagicMock

import game_actions.arena_battle as arena_battle
import game_actions.daily_tasks as daily_tasks
import game_actions.miner_action as miner_action
import json_manager
from miner.mining_service import MiningRunResult


def test_oracle_does_not_record_failed_mining_service(monkeypatch):
    device = MagicMock()
    record_calls = []

    monkeypatch.setattr(
        miner_action,
        "run_mining",
        lambda *_args, **_kwargs: MiningRunResult(False, "exception", 0),
    )
    monkeypatch.setattr(
        miner_action.cnn_model,
        "predict_image",
        lambda *_args, **_kwargs: "homeplace",
    )
    monkeypatch.setattr(miner_action.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        miner_action,
        "navigate_to_main_page",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        json_manager,
        "time_recording",
        lambda *args, **kwargs: record_calls.append((args, kwargs)),
    )

    miner_action.oracle(device, ip="emulator-5558", Cnn_model=object())

    assert record_calls == []


def test_arena_finish_failure_does_not_record_daily_completion(monkeypatch):
    record_calls = []
    monkeypatch.setattr(daily_tasks, "is_due", lambda *_args: True)
    monkeypatch.setattr(
        arena_battle,
        "run_arena_challenges",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        daily_tasks,
        "time_recording",
        lambda *args, **kwargs: record_calls.append((args, kwargs)),
    )

    daily_tasks.click_arena_challenges(MagicMock(), "emulator-5558")

    assert record_calls == []

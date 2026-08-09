"""W10 農場／每日任務 executor 的契約測試。

測試只餵既有 WS report payload，不載入 ADB、Playwright、OpenCV 或農場 UI。
"""
from __future__ import annotations

import datetime
import sys
import types
from collections.abc import Mapping

from game_actions import task_due
from game_actions.executors import farm_executor
from game_actions.task_registry import DuePolicy, TaskOutcome, get_task_definition


def test_registry_rows_keep_the_real_completion_schema_owners() -> None:
    """每日任務與農場種植不是同一個 WS task，避免接錯 row。"""
    mission = get_task_definition("main_tasks")
    farm_plant = get_task_definition("ad_rewards")
    farm_buy = get_task_definition("farm")

    assert mission.completion_policy.schema == "flat_scalar"
    assert mission.completion_policy.record_keys == ("mission_timestamp",)
    assert farm_plant.completion_policy.schema == "timestamp_record"
    assert farm_plant.completion_policy.record_keys == ("farm_plant_click",)
    assert farm_buy.completion_policy.schema == "record_time"
    assert farm_buy.completion_policy.record_keys == ("farm_seed_purchase",)
    assert mission.executors == {
        "ws": "ws_token.runner:run_device",
        "adb": "game_actions.executors.farm_executor:run_daily_client",
        "web_h5": "game_actions.executors.farm_executor:run_daily_client",
    }
    assert farm_buy.executors == {
        "ws": "ws_token.runner:run_device",
        "adb": "game_actions.executors.farm_executor:run_client",
        "web_h5": "game_actions.executors.farm_executor:run_client",
    }


def test_due_adapter_delegates_to_the_existing_due_policy(monkeypatch) -> None:
    seen: list[tuple[str, datetime.datetime]] = []
    now = datetime.datetime(2026, 8, 9, 12, 34, tzinfo=datetime.timezone.utc)

    def predicate(ip: str, resolved: datetime.datetime) -> bool:
        seen.append((ip, resolved))
        return False

    monkeypatch.setitem(task_due._REGISTRY, "W10 test due", predicate)
    monkeypatch.setattr(
        farm_executor,
        "_definition_for",
        lambda task_id: type(
            "Definition", (), {"due_policy": DuePolicy("W10 test due")}
        )(),
    )
    # The adapter itself only calls CompletionPolicy metadata; this test pins
    # that due decisions remain owned by DuePolicy/task_due.
    assert farm_executor.is_due("hellgate", "w10-device", now) is False
    assert seen == [("w10-device", now)]


def test_daily_task_completion_is_a_flat_scalar_update(monkeypatch) -> None:
    monkeypatch.setattr(farm_executor.time, "time", lambda: 1786274115.5)

    result = farm_executor.execute_ws_result("main_tasks", {})

    assert result.outcome is TaskOutcome.COMPLETED
    assert result.completion_updates == {"mission_timestamp": 1786274115.5}
    assert not isinstance(result.completion_updates["mission_timestamp"], dict)


def test_farm_seed_ad_completion_is_a_dict_update_with_count() -> None:
    payload = {
        "results": {
            "農場種子廣告": {
                "name": "農場種子廣告",
                "claimed": 2,
                "stopped": "remaining_zero",
            }
        },
        "total_claimed": 2,
    }

    result = farm_executor.execute_ws_result("ad_rewards", payload)

    assert result.outcome is TaskOutcome.COMPLETED
    assert result.completion_updates == {"farm_plant_click": {"count": 1}}
    assert isinstance(result.completion_updates["farm_plant_click"], Mapping)


def test_farm_buy_payload_produces_the_existing_record_time_shape() -> None:
    result = farm_executor.execute_ws_result(
        "farm",
        {"buy": [{"shop_id": 407, "ok": True, "target": 1}]},
        timestamp=1786274115.5,
    )

    assert result.outcome is TaskOutcome.COMPLETED
    update = result.completion_updates["farm_seed_purchase"]
    assert isinstance(update, Mapping)
    assert update["timestamp"] == 1786274115.5
    assert {"date", "datetime"} <= set(update)


def test_missing_or_non_success_payload_never_marks_completion() -> None:
    cases = (
        ("main_tasks", None),
        ("main_tasks", {"skipped": "not_claimable"}),
        ("ad_rewards", None),
        ("ad_rewards", {"results": {"商城廣告鑽石": {"claimed": 3}}}),
        ("ad_rewards", {"results": {"農場種子廣告": {"claimed": 0}}}),
        ("farm", None),
        ("farm", {"buy": [{"shop_id": 407, "ok": False, "target": 1}]}),
    )

    for task_id, payload in cases:
        result = farm_executor.execute_ws_result(task_id, payload)
        assert result.outcome is TaskOutcome.SKIPPED
        assert dict(result.completion_updates) == {}


def test_client_adapters_are_lazy_and_preserve_existing_call_signatures(monkeypatch) -> None:
    farm_calls: list[tuple[object, str, object]] = []
    fake_manager = types.SimpleNamespace(
        farm=lambda device, ip, cnn: farm_calls.append((device, ip, cnn)) or "farm-ok"
    )
    fake_farm = types.ModuleType("farm_v2")
    fake_farm.manager = fake_manager
    monkeypatch.setitem(sys.modules, "farm_v2", fake_farm)

    device = object()
    cnn = object()
    assert farm_executor.run_client(device, "farm-device", cnn) == "farm-ok"
    assert farm_calls == [(device, "farm-device", cnn)]

    mission_calls: list[str] = []
    mission = types.SimpleNamespace(
        do_allmission=lambda: mission_calls.append("daily") or "daily-ok"
    )
    assert farm_executor.run_daily_client(mission) == "daily-ok"
    assert mission_calls == ["daily"]


def test_farm_seed_ad_name_follows_the_ad_reward_table_not_a_hardcoded_string(
    monkeypatch,
) -> None:
    """釘住查表同源：`AD_NAMES[15]` 改名時 executor 必須跟著改。

    回歸守衛。原本硬編 `"農場種子廣告"`，改名後 `_farm_seed_ad_claimed` 找不到
    entry，`completion_updates_for` 靜默回空 mapping — 不拋錯、測試全過，但
    dashboard「農場種植」徽章會永遠停在未完成。live `ws_phase._ad_seed_claimed`
    走的是 `ad_reward.AD_NAMES.get(15)`，本 executor 必須同源。
    """
    from ws_token import ad_reward

    assert farm_executor._AD_FARM_SEED_CONFIG_ID == 15
    assert farm_executor._farm_seed_ad_name() == ad_reward.AD_NAMES[15]

    renamed = "農場種子廣告-renamed"
    monkeypatch.setitem(ad_reward.AD_NAMES, 15, renamed)
    payload = {"results": {renamed: {"claimed": 3}}}

    assert farm_executor._farm_seed_ad_name() == renamed
    assert farm_executor._farm_seed_ad_claimed(payload) is True
    updates = farm_executor.completion_updates_for(
        farm_executor.FARM_PLANT_TASK_ID, payload
    )
    assert updates == {"farm_plant_click": {"count": 1}}

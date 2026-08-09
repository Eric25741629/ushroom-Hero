"""農場／每日任務的 W10 executor contract。

這個模組只負責兩件事：

* 透過 registry row 取得既有 ``DuePolicy``，不複製 ``task_due`` 判斷。
* 把 WS task result 轉成 W11 可消費的 ``TaskResult``，保留既有 ledger schema。

真正的 pipeline 共用關切（force-sleep、主頁面、WS skip、狀態更新）仍由
W11 處理；本模組不呼叫 ADB、Playwright，也不在 import 時載入農場 UI。
"""
from __future__ import annotations

import datetime
import time
from collections.abc import Mapping
from typing import Any, Callable

from game_actions.task_registry import (
    CompletionPolicy,
    TaskDefinition,
    TaskOutcome,
    TaskResult,
    get_task_definition,
)


DAILY_TASK_ID = "main_tasks"
# 「農場種植」徽章的真實 WS owner 是 ad_rewards（config 15），不是 farm。
FARM_PLANT_TASK_ID = "ad_rewards"
FARM_BUY_TASK_ID = "farm"
# ad_reward config_id：農場種子廣告。與 ws_phase._AD_FARM_SEED_CONFIG_ID 同源。
_AD_FARM_SEED_CONFIG_ID = 15
_FARM_SEED_SHOP_ID = 407
_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _definition_for(task_id: str) -> TaskDefinition:
    """取得 registry row；未知 task 讓 registry 保持明確失敗。"""
    return get_task_definition(task_id)


def is_due(
    task_id: str,
    ip: str,
    now: datetime.datetime | None = None,
) -> bool:
    """委派 row 的 due policy，不重寫既有 ``task_due`` predicate。"""
    return _definition_for(task_id).due_policy.is_due(ip, now)


def _skipped(detail: str) -> TaskResult:
    return TaskResult(TaskOutcome.SKIPPED, detail=detail)


def _completion_update(
    policy: CompletionPolicy,
    key: str,
    value: bool | int | float | str | Mapping[str, bool | int | float | str],
) -> dict[str, Any]:
    """以 CompletionPolicy 驗證 update key，避免 executor 寫錯 ledger。"""
    if key not in policy.record_keys:
        raise ValueError(
            f"completion key {key!r} 不屬於 schema {policy.schema!r}: "
            f"{policy.record_keys!r}"
        )
    return {key: value}


def _timestamp(timestamp: float | None) -> float:
    return time.time() if timestamp is None else float(timestamp)


def _record_time_value(timestamp: float) -> dict[str, str | float]:
    dt = datetime.datetime.fromtimestamp(timestamp, _TPE)
    return {
        "timestamp": timestamp,
        "date": dt.strftime("%Y-%m-%d"),
        "datetime": dt.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _farm_seed_ad_name() -> str | None:
    """由 `ad_reward.AD_NAMES` 查表取名，與 live `ws_phase._ad_seed_claimed` 同源。

    原本硬編 `"農場種子廣告"`：`AD_NAMES[15]` 一改名，本模組就查不到那筆
    entry，`completion_updates_for` 會靜默回傳空 mapping（dashboard 徽章永遠
    停在未完成，且不拋錯）。延遲 import 讓 registry 契約測試不必載入 ws_token。
    """
    from ws_token import ad_reward

    return ad_reward.AD_NAMES.get(_AD_FARM_SEED_CONFIG_ID)


def _farm_seed_ad_claimed(payload: Mapping[str, Any]) -> bool:
    """沿用 ws_phase 的 config 15 成功／maxed 語意。"""
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return False
    entry = results.get(_farm_seed_ad_name())
    if not isinstance(entry, Mapping):
        return False
    claimed = entry.get("claimed")
    if isinstance(claimed, int) and claimed > 0:
        return True
    skipped = entry.get("skipped")
    return isinstance(skipped, str) and skipped.startswith("maxed")


def _farm_seed_bought(payload: Mapping[str, Any]) -> bool:
    """沿用 ws_phase 的 shop 407 成功語意，未成功不標記今日完成。"""
    buy = payload.get("buy")
    if not isinstance(buy, (list, tuple)):
        return False
    for entry in buy:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("shop_id") != _FARM_SEED_SHOP_ID or entry.get("ok") is not True:
            continue
        try:
            if int(entry.get("target") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def completion_updates_for(
    task_id: str,
    payload: Mapping[str, Any],
    *,
    timestamp: float | None = None,
) -> Mapping[str, Any]:
    """從既有 WS payload 產生 ledger update；無成功證據時回傳空 mapping。"""
    definition = _definition_for(task_id)
    policy = definition.completion_policy
    stamp = _timestamp(timestamp)

    if task_id == DAILY_TASK_ID:
        # _substantive_done 對空 dict 視為完成；只有明確 skipped 不算完成。
        if "skipped" in payload:
            return {}
        return _completion_update(policy, "mission_timestamp", stamp)

    if task_id == FARM_PLANT_TASK_ID:
        if not _farm_seed_ad_claimed(payload):
            return {}
        # 既有 ws_phase/_mark_farm_plant_done 固定寫 count=1；不要把 WS
        # claimed 次數改成 dashboard 原本的 farm_v2 count 語意。
        return _completion_update(policy, "farm_plant_click", {"count": 1})

    if task_id == FARM_BUY_TASK_ID:
        if not _farm_seed_bought(payload):
            return {}
        return _completion_update(
            policy,
            "farm_seed_purchase",
            _record_time_value(stamp),
        )

    return {}


def execute_ws_result(
    task_id: str,
    payload: Mapping[str, Any] | None,
    *,
    error: str | None = None,
    timestamp: float | None = None,
) -> TaskResult:
    """將單一 WS report task 轉成 W11 的標準結果。

    缺 payload、明確 skipped 或 payload schema 不完整時保守回 ``SKIPPED``，
    絕不產生 completion update；WS runner 的明確錯誤則保留為 failure。
    """
    _definition_for(task_id)
    if error:
        return TaskResult(TaskOutcome.PERMANENT_FAILURE, detail=error)
    if not isinstance(payload, Mapping):
        return _skipped("缺少 WS payload，保留待辦")
    if "skipped" in payload:
        return _skipped(f"WS skipped: {payload.get('skipped')}")

    updates = completion_updates_for(task_id, payload, timestamp=timestamp)
    if not updates:
        return _skipped("payload 沒有足夠的完成證據，保留待辦")
    return TaskResult(
        TaskOutcome.COMPLETED,
        detail=f"{task_id} payload 已完成",
        completion_updates=updates,
    )


def run_client(
    device: Any,
    ip: str,
    cnn_model: Any,
    *,
    action: Callable[[], Any] | None = None,
) -> Any:
    """W11 client executor：延遲委派既有 farm_v2 action。"""
    if action is not None:
        return action()
    from farm_v2 import manager as farm_manager

    return farm_manager.farm(device, ip, cnn_model)


def run_daily_client(
    mission_manager: Any,
    *,
    action: Callable[[], Any] | None = None,
) -> Any:
    """W11 client executor：延遲委派既有 Mission action。"""
    if action is not None:
        return action()
    return mission_manager.do_allmission()


# 語意化別名供 W11 以 task id 直接消費；兩者都不改既有 action 參數。
run_farm = run_client
run_daily_tasks = run_daily_client


__all__ = [
    "DAILY_TASK_ID",
    "FARM_BUY_TASK_ID",
    "FARM_PLANT_TASK_ID",
    "completion_updates_for",
    "execute_ws_result",
    "is_due",
    "run_client",
    "run_daily_client",
    "run_daily_tasks",
    "run_farm",
]

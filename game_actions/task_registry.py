"""每日任務註冊表的輕量資料模型與唯讀 API。

本模組只描述既有任務事實，不匯入 runner、Playwright 或 ADB 執行層，也不負責
排程與執行。執行器欄位保存既有入口的穩定參照字串，後續遷移才能逐項接線。
"""
from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Literal


BackendKind = Literal["adb", "web_h5", "ws"]
TaskExecutor = str
CompletionScalar = bool | int | float | str
CompletionValue = CompletionScalar | Mapping[str, CompletionScalar]
CompletionSchema = Literal[
    "none", "daily_record", "flat_scalar", "timestamp_record", "record_time"
]


class TaskOutcome(str, Enum):
    """單一任務的標準結果；中斷不屬於失敗。"""

    COMPLETED = "completed"
    SKIPPED = "skipped"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    INTERRUPTED = "interrupted"


def _empty_updates() -> dict[str, CompletionValue]:
    return {}


def _freeze_completion_value(value: CompletionValue) -> CompletionValue:
    if isinstance(value, Mapping):
        return MappingProxyType(dict(value))
    return value


@dataclass(frozen=True)
class TaskResult:
    outcome: TaskOutcome
    detail: str = ""
    retry_after_sec: float | None = None
    completion_updates: Mapping[str, CompletionValue] = field(
        default_factory=_empty_updates
    )

    def __post_init__(self) -> None:
        updates = MappingProxyType({
            key: _freeze_completion_value(value)
            for key, value in self.completion_updates.items()
        })
        object.__setattr__(self, "completion_updates", updates)
        if self.retry_after_sec is not None:
            if self.outcome is not TaskOutcome.RETRYABLE_FAILURE:
                raise ValueError("retry_after_sec 僅適用 RETRYABLE_FAILURE")
            if self.retry_after_sec < 0:
                raise ValueError("retry_after_sec 不可為負數")
        if self.outcome is TaskOutcome.INTERRUPTED and updates:
            raise ValueError("INTERRUPTED 不可寫入 completion_updates")


@dataclass(frozen=True)
class DuePolicy:
    """延遲委派 ``task_due._REGISTRY``，不複製任何 due 判斷。"""

    registry_key: str | None = None

    def is_due(
        self, ip: str, now: datetime.datetime | None = None
    ) -> bool:
        if self.registry_key is None:
            return True
        # 延遲 import，讀 registry 時不會連帶載入個別任務的重依賴。
        from game_actions import task_due

        try:
            predicate = task_due._REGISTRY[self.registry_key]
        except KeyError:
            raise KeyError(
                f"unknown task for due-check: {self.registry_key!r}"
            ) from None
        return bool(predicate(ip, task_due._resolve_now(now)))


@dataclass(frozen=True)
class CompletionPolicy:
    """描述既有 ledger schema；真正的寫入仍留在現行執行層。"""

    schema: CompletionSchema = "none"
    record_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema == "none" and self.record_keys:
            raise ValueError("none completion schema 不可帶 record key")
        if self.schema != "none" and not self.record_keys:
            raise ValueError("completion schema 必須帶 record key")


@dataclass(frozen=True)
class RetryPolicy:
    """重試宣告；預設一次且不重試。"""

    max_attempts: int = 1
    delay_sec: float = 0.0
    retryable_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 至少為 1")
        if self.delay_sec < 0:
            raise ValueError("delay_sec 不可為負數")


def _empty_executors() -> dict[BackendKind, TaskExecutor]:
    return {}


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    display_name: str
    order: int
    ws_display_name: str | None = None
    enabled_key: str | None = None
    due_policy: DuePolicy = field(default_factory=DuePolicy)
    executors: Mapping[BackendKind, TaskExecutor] = field(
        default_factory=_empty_executors
    )
    completion_policy: CompletionPolicy = field(default_factory=CompletionPolicy)
    skip_when_ws_done: bool | tuple[str, ...] = False
    needs_main_page: bool = False
    record_name: str | None = None
    timeout_sec: float | None = None
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    time_window: tuple[int, int] | None = None
    device_excludes: frozenset[str] = frozenset()
    batch_cap: int | None = None
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.task_id):
            raise ValueError(f"task_id 必須是穩定 ASCII id: {self.task_id!r}")
        has_client = "adb" in self.executors or "web_h5" in self.executors
        if has_client and self.order <= 0:
            raise ValueError("client task 的 order 必須為正整數")
        if not has_client and self.order != 0:
            raise ValueError("WS-only task 的 order 必須使用 sentinel 0")
        if self.time_window is not None:
            start, end = self.time_window
            if not (0 <= start < end <= 24):
                raise ValueError("time_window 必須位於同一天且 start < end")
        object.__setattr__(self, "executors", MappingProxyType(dict(self.executors)))


_NONE = CompletionPolicy()
_MISSION = CompletionPolicy("flat_scalar", ("mission_timestamp",))
_FARM_PLANT = CompletionPolicy("timestamp_record", ("farm_plant_click",))
_FARM_SEED = CompletionPolicy("record_time", ("farm_seed_purchase",))


def _daily(*keys: str) -> CompletionPolicy:
    return CompletionPolicy("daily_record", tuple(keys))


def _executors(
    task_id: str,
    pipeline_label: str | None = None,
    client_backends: tuple[BackendKind, ...] = ("adb", "web_h5"),
    include_ws: bool = True,
    client_executor: str | None = None,
) -> Mapping[BackendKind, TaskExecutor]:
    # W7 先登記現行共享 entrypoint；已遷移任務改登記專用 executor。
    refs: dict[BackendKind, TaskExecutor] = {}
    if include_ws:
        refs["ws"] = "ws_token.runner:run_device"
    if pipeline_label is not None:
        for backend in client_backends:
            refs[backend] = client_executor or "game_actions.daily_pipeline:run"
    return MappingProxyType(refs)


def _task(
    task_id: str,
    display_name: str,
    order: int,
    *,
    ws_display_name: str | None = None,
    pipeline_label: str | None = None,
    skip: tuple[str, ...] = (),
    conditional_skip: bool = False,
    enabled_key: str | None = None,
    due_key: str | None = None,
    completion: CompletionPolicy = _NONE,
    client_backends: tuple[BackendKind, ...] = ("adb", "web_h5"),
    needs_main_page: bool = False,
    record_name: str | None = None,
    timeout_sec: float | None = None,
    time_window: tuple[int, int] | None = None,
    device_excludes: frozenset[str] = frozenset(),
    batch_cap: int | None = None,
    tags: frozenset[str] = frozenset(),
    include_ws: bool = True,
    client_executor: str | None = None,
) -> TaskDefinition:
    if conditional_skip:
        tags = tags | {"conditional-ws-skip"}
    return TaskDefinition(
        task_id=task_id,
        display_name=display_name,
        order=order,
        ws_display_name=ws_display_name,
        enabled_key=enabled_key,
        due_policy=DuePolicy(due_key),
        executors=_executors(
            task_id,
            pipeline_label,
            client_backends,
            include_ws=include_ws,
            client_executor=client_executor,
        ),
        completion_policy=completion,
        skip_when_ws_done=skip or False,
        needs_main_page=needs_main_page,
        record_name=record_name,
        timeout_sec=timeout_sec,
        time_window=time_window,
        device_excludes=device_excludes,
        batch_cap=batch_cap,
        tags=tags,
    )


# 宣告順序先列實際 WS 流程，再列 client-only；order 只表示 client pipeline。
# WS-only 任務使用 sentinel 0，client 任務依 W5 的 28 項既有順序使用 10 間隔。
_DIRECT_SKIP = frozenset({"direct-client-skip"})
_PARTIAL_SKIP = frozenset({"partial-client-skip"})
_TASKS: tuple[TaskDefinition, ...] = (
    _task("carpark", "車位檢查", 30, ws_display_name="跨界停車", pipeline_label="車位檢查", tags=frozenset({"ws-first"})),
    _task("mount_sprint", "坐騎強化", 110, ws_display_name="坐騎衝刺", pipeline_label="坐騎強化", skip=("坐騎強化",), enabled_key="enable_mount_sprint", due_key="坐騎衝刺", needs_main_page=True),
    TaskDefinition(
        task_id="main_tasks",
        display_name="所有日常任務",
        order=140,
        ws_display_name="每日任務",
        executors={
            "ws": "ws_token.runner:run_device",
            "adb": "game_actions.executors.farm_executor:run_daily_client",
            "web_h5": "game_actions.executors.farm_executor:run_daily_client",
        },
        completion_policy=_MISSION,
        skip_when_ws_done=("所有日常任務",),
        needs_main_page=True,
        time_window=(20, 23),
        tags=_DIRECT_SKIP,
    ),
    _task("league_solo", "烈焰山洞與魔法劇場", 0),
    _task("redpack", "紅包檢查", 10, pipeline_label="紅包檢查", skip=("紅包檢查",), client_backends=("web_h5",), tags=_DIRECT_SKIP),
    _task("mail", "郵件領取", 0, enabled_key="mail_claim"),
    _task("idle_reward", "點擊寶箱", 60, ws_display_name="離線收益", pipeline_label="點擊寶箱", skip=("點擊寶箱",), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("ad_rewards", "廣告獎勵", 0, completion=_FARM_PLANT),
    _task("turntable", "轉盤金幣", 280, pipeline_label="轉盤金幣", skip=("轉盤金幣",), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("tycoon", "傳奇大亨", 0, enabled_key="tycoon"),
    TaskDefinition(
        task_id="farm",
        display_name="農場任務",
        order=50,
        executors={
            "ws": "ws_token.runner:run_device",
            "adb": "game_actions.executors.farm_executor:run_client",
            "web_h5": "game_actions.executors.farm_executor:run_client",
        },
        completion_policy=_FARM_SEED,
        skip_when_ws_done=("農場任務",),
        needs_main_page=True,
        tags=_DIRECT_SKIP | {"conditional-ws-skip"},
    ),
    _task("harvest_card", "豐收卡", 0),
    _task("dungeon", "萬神試煉", 220, ws_display_name="副本管家", pipeline_label="萬神試煉", skip=("萬神試煉",), conditional_skip=True, enabled_key="enable_wanshen", due_key="萬神試煉", completion=_daily("萬神試煉"), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("hellgate", "地獄之門", 40, pipeline_label="地獄之門", skip=("地獄之門",), enabled_key="enable_hellgate", due_key="地獄之門", completion=_daily("地獄之門"), record_name="地獄之門"),
    _task("rogue", "萬神試煉週獎勵", 0, timeout_sec=6.0),
    _task("ladder_reward", "天梯每週獎勵", 210, pipeline_label="天梯每週獎勵", skip=("天梯每週獎勵",), due_key="天梯每週獎勵", client_backends=("web_h5",), tags=_DIRECT_SKIP),
    _task("seven_login", "七日登入獎勵", 20, pipeline_label="七日登入獎勵", skip=("七日登入獎勵",), completion=_daily("七日登入"), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("cloud_ladder", "雲端戰鬥", 230, ws_display_name="雲纏天梯", pipeline_label="雲端戰鬥", skip=("雲端戰鬥",), enabled_key="enable_cloud_battle", due_key="雲端戰鬥", needs_main_page=True, tags=_DIRECT_SKIP),
    _task("arena", "競技場挑戰", 270, pipeline_label="競技場挑戰", skip=("競技場挑戰",), enabled_key="enable_arena", due_key="競技場挑戰", completion=_daily("arena_challenges"), needs_main_page=True),
    _task("escort", "賞金之路", 200, pipeline_label="賞金之路", skip=("賞金之路",), enabled_key="enable_escort", due_key="賞金之路", completion=_daily("escort_last_run"), client_backends=("web_h5",)),
    _task("statue", "菇菇雕像每週", 160, ws_display_name="菇菇雕像", pipeline_label="菇菇雕像每週", due_key="菇菇雕像每週", needs_main_page=True),
    _task("guild", "家族任務", 70, pipeline_label="家族任務", skip=("家族任務",), completion=_daily("donate_family"), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("steward", "商店購買", 100, ws_display_name="管家", pipeline_label="商店購買", skip=("商店購買",), completion=_daily("Store"), record_name="Store", tags=_DIRECT_SKIP),
    _task("relic", "遺物強化", 0),
    _task("relic_sprint", "遺物衝刺", 0),
    _task("gacha", "抽技能夥伴", 90, ws_display_name="技能夥伴抽獎", pipeline_label="抽技能夥伴", skip=("抽技能夥伴",), device_excludes=frozenset({"emulator-5558"}), tags=_PARTIAL_SKIP),
    _task("gacha_free", "技能夥伴免費抽獎", 0, due_key="抽技能夥伴"),
    _task("kungfu_store", "武道會競猜商店", 0),
    _task("kungfu_worship", "菇菇武道會", 150, pipeline_label="菇菇武道會", skip=("菇菇武道會",), due_key="菇菇武道會", completion=_daily("mushroom_arena_cycle_start", "mushroom_arena_daily"), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("pay_mall", "付費商城免費領取", 0),
    _task("spirit", "領取守護靈", 80, pipeline_label="領取守護靈", skip=("領取守護靈",), device_excludes=frozenset({"emulator-5558"}), record_name="guardian_spirit", tags=_DIRECT_SKIP),
    _task("secret_jewel", "神秘寶石", 0),
    _task("workshop", "加工坊", 0),
    _task("couple", "好友每日禮物", 250, ws_display_name="伴侶", pipeline_label="好友每日禮物", skip=("好友每日禮物",), needs_main_page=True, tags=_DIRECT_SKIP),
    _task("dragon_realm", "龍骸聖域", 180, pipeline_label="龍骸聖域", due_key="龍骸聖域", client_backends=("web_h5",), client_executor="game_actions.executors.single_backend_executor:run_dragon_realm"),
    _task("xwar_idle", "跨服戰放置獎勵", 0, enabled_key="xwar_idle"),
    _task("sea_season", "航海任務 (Sea)", 170, ws_display_name="航海賽季", pipeline_label="航海任務 (Sea)", skip=("航海任務 (Sea)",), due_key="航海", needs_main_page=True),
    _task("star_explore", "星際探索", 0),
    _task("mining", "挖礦/Oracle", 130, pipeline_label="挖礦/Oracle", skip=("挖礦/Oracle",), enabled_key="enable_mining", completion=_daily("挖礦"), needs_main_page=True, record_name="挖礦", tags=_DIRECT_SKIP),
    # W8：保留 WS runner 的 live 入口，client 兩後端改由 lamp adapter 接管；
    # skip/batch metadata 供後續 W11 registry loop 消費，這輪不搬 _run_tasks。
    TaskDefinition(
        task_id="lamp",
        display_name="開神燈",
        order=260,
        executors={
            "ws": "ws_token.runner:run_device",
            "adb": "game_actions.executors.lamp_executor:run_client",
            "web_h5": "game_actions.executors.lamp_executor:run_client",
        },
        skip_when_ws_done=("開神燈",),
        batch_cap=20,
        tags=_DIRECT_SKIP,
    ),
    _task("main_tasks_late", "每日任務尾端補領", 0),
    _task("main_chapter_kills", "主線擊敗敵人", 0, enabled_key="main_chapter_kills.enabled"),
    _task("daily_acceleration", "每日加速", 120, pipeline_label="每日加速", due_key="每日加速", include_ws=False),
    _task("fannaoxiao", "煩惱消", 190, pipeline_label="煩惱消", due_key="煩惱消", client_backends=("web_h5",), include_ws=False, client_executor="game_actions.executors.single_backend_executor:run_fannaoxiao"),
    _task("biweekly", "雙週副本", 240, pipeline_label="雙週副本", enabled_key="enable_biweekly", due_key="雙週副本", include_ws=False, tags=frozenset({"emulator-5556-only"})),
)

_BY_ID: Mapping[str, TaskDefinition] = MappingProxyType(
    {definition.task_id: definition for definition in _TASKS}
)


def get_task_definition(task_id: str) -> TaskDefinition:
    """依穩定 id 讀取定義；未知 id 明確失敗。"""
    try:
        return _BY_ID[task_id]
    except KeyError:
        raise KeyError(f"unknown task definition: {task_id!r}") from None


def iter_task_definitions() -> tuple[TaskDefinition, ...]:
    """依 registry 宣告順序回傳不可變快照（WS projection 後接 client-only）。"""
    return _TASKS


def task_ids() -> tuple[str, ...]:
    return tuple(definition.task_id for definition in _TASKS)


def ws_task_ids() -> tuple[str, ...]:
    """依 run_device 實際執行順序投影 WS 任務。"""
    return tuple(
        definition.task_id for definition in _TASKS if "ws" in definition.executors
    )


def _pipeline_order(definition: TaskDefinition) -> int:
    return definition.order


def iter_pipeline_task_definitions() -> tuple[TaskDefinition, ...]:
    """依既有 daily_pipeline 的 28 項 client 順序回傳定義。"""
    client_tasks = (
        definition
        for definition in _TASKS
        if "adb" in definition.executors or "web_h5" in definition.executors
    )
    return tuple(sorted(client_tasks, key=_pipeline_order))


def ws_to_pipeline_skip_mapping(
    *, include_conditional: bool = False
) -> Mapping[str, tuple[str, ...]]:
    """回傳 WS 完成後的 pipeline label；條件式對照預設不混入無條件表。"""
    mapping: dict[str, tuple[str, ...]] = {}
    for definition in _TASKS:
        names = definition.skip_when_ws_done
        if not isinstance(names, tuple):
            continue
        if not include_conditional and "conditional-ws-skip" in definition.tags:
            continue
        mapping[definition.task_id] = names
    return MappingProxyType(mapping)


def pipeline_display_names() -> tuple[str, ...]:
    """完整 28 項 client labels，順序與 live daily pipeline 相同。"""
    return tuple(
        definition.display_name for definition in iter_pipeline_task_definitions()
    )


__all__ = [
    "CompletionPolicy", "DuePolicy", "RetryPolicy", "TaskDefinition",
    "TaskOutcome", "TaskResult", "get_task_definition",
    "iter_pipeline_task_definitions", "iter_task_definitions",
    "pipeline_display_names", "task_ids", "ws_task_ids",
    "ws_to_pipeline_skip_mapping",
]

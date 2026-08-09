"""`daily_pipeline._run_tasks` 的順序與 gating 特徵化測試。

本檔只建立 fake `DailyContext` 與 spy collaborators；測試不連真實裝置，
也不改動既有 `tests/test_daily_pipeline.py`。這裡釘住的是目前 live pipeline
的行為契約，後續 registry 遷移不得悄悄改變順序或 gating。
"""
from __future__ import annotations

import datetime
import importlib
import logging
import sys
import time
import types

import pytest


def _RECORDED_NOOP(*_args, **_kwargs) -> None:
    """取代真實 `json_manager.time_recording`：絕不寫入裝置 ledger。"""


def _IS_DUE_STUB(*_args, **_kwargs) -> bool:
    """取代真實 `task_due.is_due`：順序斷言不得依賴當下時間。"""
    return True


_STUBBED: list[tuple[str, types.ModuleType | None, tuple[object, str] | None]] = []


def _stub_module(name: str, **attrs):
    """強制安裝 stub，並記錄原狀以便 import 後還原。

    早期版本用 ``sys.modules.setdefault``：若同一個 pytest session 內有其他
    測試檔（例如 `test_farm_executor.py`）先載入真實 `json_manager` /
    `game_actions.task_due`，setdefault 會靜默變成 no-op，`daily_pipeline`
    於是綁到真實模組 — 真實 `time_recording` 會寫進 repo 根目錄的裝置 ledger
    （如 `fc65396d.json`），而真實 `task_due.is_due` 讓任務順序隨當下時間浮動。
    因此改為強制覆寫；`daily_pipeline` 以 from-import 綁住 stub 物件後，
    `_restore_stubbed_modules()` 再把 sys.modules 與父套件屬性還原，避免污染
    後續測試檔。
    """
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)

    previous = sys.modules.get(name)
    parent_state: tuple[object, str] | None = None
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            # `from pkg import mod` 優先讀父套件屬性，光改 sys.modules 不夠。
            parent_state = (parent, child)
            _STUBBED.append((name, previous, parent_state))
            setattr(parent, child, module)
            sys.modules[name] = module
            return module

    _STUBBED.append((name, previous, parent_state))
    sys.modules[name] = module
    return module


def _restore_stubbed_modules() -> None:
    """還原被強制覆寫的模組，避免 stub 洩漏到其他測試檔。"""
    for name, previous, parent_state in reversed(_STUBBED):
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        if parent_state is not None:
            parent, child = parent_state
            if previous is None:
                if hasattr(parent, child):
                    delattr(parent, child)
            else:
                setattr(parent, child, previous)
    _STUBBED.clear()


def _install_lightweight_dependencies() -> None:
    """在 import daily_pipeline 前隔離 ADB/OCR/Playwright 相依。"""
    _stub_module(
        "bot_state",
        check_force_sleep=lambda _ip: False,
        update_state=lambda *args, **kwargs: None,
    )
    _stub_module(
        "config_manager",
        get_device_config=lambda _ip: {"enable_shop_manager": True, "mining_duration_min": 6},
    )
    _stub_module("daily_gift_task", buy_gift_for_friend_daily=lambda *a, **k: None)
    _stub_module(
        "new_battle",
        hell_door=lambda *a, **k: None,
        run_weekly_cloud_fighting_single=lambda *a, **k: None,
    )
    _stub_module("rank_events", park_spring=lambda *a, **k: None)
    _stub_module("Store", buy_store=lambda *a, **k: None)
    _stub_module("Skill", get_skill_and_partner=lambda *a, **k: None)
    _stub_module("Sea", sea=lambda *a, **k: None)

    everyday = _stub_module("everyday_mission")
    guardian = _stub_module(
        "everyday_mission.Guardian_Spirit_manger",
        get_Guardian_Spirit=lambda *a, **k: None,
    )
    everyday.Guardian_Spirit_manger = guardian

    farm = _stub_module("farm_v2")
    farm.manager = _stub_module("farm_v2.manager", farm=lambda *a, **k: None)

    _stub_module("game_actions.carpark_scheduler", run_carpark_check_if_due=lambda *a, **k: None)
    _stub_module("game_actions.dragon_realm_scheduler", run_dragon_realm_if_due=lambda *a, **k: None)
    _stub_module("game_actions.fannaoxiao_scheduler", run_fannaoxiao_if_due=lambda *a, **k: None)
    _stub_module("game_actions.escort_scheduler", run_escort_if_due=lambda *a, **k: None)
    _stub_module("game_actions.ladder_reward_weekly", run_ladder_reward_if_due=lambda *a, **k: None)
    _stub_module("game_actions.seven_login_daily", run_seven_login_if_due=lambda *a, **k: None)
    _stub_module(
        "game_actions.daily_tasks",
        click_arena_challenges=lambda *a, **k: None,
        daily_acceleration=lambda *a, **k: None,
    )
    _stub_module(
        "game_actions.dungeon_scheduler",
        _run_biweekly_dungeon=lambda *a, **k: None,
        _run_weekly_dungeon=lambda *a, **k: None,
    )
    _stub_module("game_actions.lamp_scheduler", _run_lamp_if_due=lambda *a, **k: None)
    _stub_module("game_actions.miner_action", oracle=lambda *a, **k: None)
    _stub_module("game_actions.redpack_scheduler", run_redpack_check_if_due=lambda *a, **k: None)
    _stub_module("game_actions.statue_weekly", run_statue_weekly_if_due=lambda *a, **k: None)
    _stub_module(
        "game_actions.periodic_tasks",
        _run_periodic_cycle=lambda *a, **k: None,
        mushroom_arena=lambda *a, **k: None,
        should_execute_mushroom_arena=lambda *a, **k: True,
    )
    _stub_module("game_actions.reward_manager", reward=lambda *a, **k: None)
    _stub_module("game_actions.skill_manager", switch_skill_h5=lambda *a, **k: None)
    _stub_module(
        "game_actions.stage_guard",
        _run_at_main_page=lambda *a, **k: "主頁面",
        get_stage_with_check=lambda *a, **k: "主頁面",
    )
    _stub_module("game_actions.task_due", is_due=_IS_DUE_STUB)
    _stub_module("game_actions.special_wanshen")

    runtime = _stub_module("runtime_services")
    runtime.device_runtime_service = _stub_module(
        "runtime_services.device_runtime_service",
        ForceSleepRequested=type("ForceSleepRequested", (Exception,), {}),
    )
    _stub_module(
        "json_manager",
        is_record_expired=lambda *a, **k: True,
        return_time=lambda *a, **k: None,
        should_execute_sea_with_cooldown=lambda *a, **k: True,
        time_recording=_RECORDED_NOOP,
    )
    _stub_module("tools", click_white=lambda *a, **k: None)
    # `utils` 是 namespace package（無 __init__.py）。先載入真實套件再掛 stub
    # 子模組：早期版本用 setdefault 塞一個裸 ModuleType，該假物件沒有
    # `__path__`，後續測試檔 import `utils.mumu_control` 就會炸
    # `'utils' is not a package`。這裡只覆寫兩個子模組，父套件保持真實。
    importlib.import_module("utils")
    _stub_module(
        "utils.logging_utils", logger=logging.getLogger("test_daily_pipeline_order")
    )
    _stub_module(
        "utils.screenshot_helpers",
        log_main_page_mismatch=lambda *a, **k: None,
        save_error_screenshot=lambda *a, **k: None,
    )


_install_lightweight_dependencies()
try:
    # daily_pipeline 以 from-import 取用這些符號，import 完成後就已綁定 stub
    # 函式物件；此時把 sys.modules 還原，stub 不會外洩給其他測試檔。
    sys.modules.pop("game_actions.daily_pipeline", None)
    pipeline = importlib.import_module("game_actions.daily_pipeline")
finally:
    _restore_stubbed_modules()
    # 本檔這份 daily_pipeline 綁的是 stub（含 stub 的 ForceSleepRequested）。
    # 留在 sys.modules 會讓 tests/test_daily_pipeline.py 沿用同一份而與真實
    # 例外類別對不起來，因此撤出快取，讓其他測試檔各自 import 自己的版本。
    sys.modules.pop("game_actions.daily_pipeline", None)
    _game_actions_pkg = sys.modules.get("game_actions")
    if _game_actions_pkg is not None:
        try:
            delattr(_game_actions_pkg, "daily_pipeline")
        except AttributeError:
            pass


EXPECTED_ORDER = [
    "紅包檢查", "七日登入獎勵", "車位檢查", "地獄之門", "農場任務", "點擊寶箱",
    "家族任務", "領取守護靈", "抽技能夥伴", "商店購買", "坐騎強化", "每日加速",
    "競技場挑戰", "挖礦/Oracle", "所有日常任務", "菇菇武道會", "菇菇雕像每週",
    "航海任務 (Sea)", "龍骸聖域", "煩惱消", "賞金之路", "天梯每週獎勵",
    "萬神試煉", "雲端戰鬥", "雙週副本", "好友每日禮物", "開神燈", "轉盤金幣",
]


class _Device:
    def __init__(self):
        self.events: list[tuple] = []

    def tap(self, *args):
        self.events.append(("tap", args))

    def app_start(self, package):
        self.events.append(("app_start", package))

    def app_stop(self, package):
        self.events.append(("app_stop", package))


class _Manager:
    def __init__(self, events: list[str], family_label: str = "家族任務"):
        self.events = events
        self.family_label = family_label

    def go_to_family(self):
        pass

    def do_allmission(self):
        pass

    def spin_and_send_gold(self):
        return True


def _build_context(ip: str = "emulator-5554", **overrides):
    events: list[str] = []
    device = _Device()
    family = _Manager(events)
    wheel = _Manager(events)
    mission = _Manager(events)
    values = dict(
        d=device,
        ip=ip,
        Cnn_model=object(),
        clf=object(),
        rl_recorder=object(),
        current_time=time.struct_time((2026, 8, 9, 21, 0, 0, 5, 221, -1)),
        enable_dungeon_manager=True,
        wheel_manager=wheel,
        mission_manager=mission,
        family_manager=family,
        enable_hellgate=True,
        enable_arena=True,
        enable_mining=True,
        enable_wanshen=True,
        enable_cloud_battle=True,
        enable_biweekly=True,
        ws_done=frozenset(),
        special_wanshen_claimed=False,
    )
    values.update(overrides)
    return pipeline.DailyContext(**values), events, device


@pytest.fixture
def patched_pipeline(monkeypatch):
    events: list[str] = []

    def at_main_page(_d, _ip, _model, task_name=None, _reason=None, fn=None, **_kwargs):
        events.append(task_name)
        if fn is not None:
            fn()
        return "主頁面"

    monkeypatch.setattr(pipeline, "_run_at_main_page", at_main_page)
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pipeline.time, "localtime", lambda: time.struct_time(
        (2026, 8, 9, 21, 0, 0, 5, 221, -1)
    ))
    monkeypatch.setattr(pipeline, "get_stage_with_check", lambda *a, **k: "主頁面")
    monkeypatch.setattr(pipeline, "click_white", lambda *a, **k: None)

    monkeypatch.setattr(pipeline, "run_redpack_check_if_due", lambda *a, **k: events.append("紅包檢查"))
    monkeypatch.setattr(pipeline, "run_seven_login_if_due", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "run_carpark_check_if_due", lambda *a, **k: events.append("車位檢查"))
    monkeypatch.setattr(pipeline.new_battle, "hell_door", lambda *a, **k: events.append("地獄之門"))
    monkeypatch.setattr(pipeline, "get_Guardian_Spirit", lambda *a, **k: events.append("領取守護靈"))
    monkeypatch.setattr(pipeline, "get_skill_and_partner", lambda *a, **k: events.append("抽技能夥伴"))
    monkeypatch.setattr(pipeline.Store, "buy_store", lambda *a, **k: events.append("商店購買"))
    monkeypatch.setattr(pipeline, "daily_acceleration", lambda *a, **k: events.append("每日加速"))
    # 這些 action 都由外層 `_run_at_main_page` 記錄，避免 spy 重複計數。
    monkeypatch.setattr(pipeline, "_run_periodic_cycle", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "run_statue_weekly_if_due", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "run_dragon_realm_if_due", lambda *a, **k: events.append("龍骸聖域"))
    monkeypatch.setattr(pipeline, "run_fannaoxiao_if_due", lambda *a, **k: events.append("煩惱消"))
    monkeypatch.setattr(pipeline, "run_escort_if_due", lambda *a, **k: events.append("賞金之路"))
    monkeypatch.setattr(pipeline, "run_ladder_reward_if_due", lambda *a, **k: events.append("天梯每週獎勵"))
    monkeypatch.setattr(pipeline, "_run_weekly_dungeon", lambda *a, **k: events.append("萬神試煉"))
    # 雲端戰鬥由 `_run_at_main_page` 記錄；函式本身不重複記錄。
    monkeypatch.setattr(pipeline.new_battle, "run_weekly_cloud_fighting_single",
                        lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_biweekly_dungeon", lambda *a, **k: events.append("雙週副本"))
    # 好友禮物同樣由 `_run_at_main_page` 記錄。
    monkeypatch.setattr(pipeline.daily_gift_task, "buy_gift_for_friend_daily",
                        lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_run_lamp_if_due", lambda *a, **k: events.append("開神燈"))
    monkeypatch.setattr(pipeline.bot_state, "check_force_sleep", lambda _ip: False)
    monkeypatch.setattr(pipeline.task_due, "is_due", lambda *a, **k: True)
    return events


def test_run_tasks_preserves_the_28_task_order(patched_pipeline):
    ctx, _unused, _device = _build_context()
    pipeline.run(ctx)

    assert patched_pipeline == EXPECTED_ORDER


def test_registry_loop_returns_ws_shaped_run_report(patched_pipeline):
    ctx, _unused, _device = _build_context()

    report = pipeline.run(ctx)

    assert isinstance(report, pipeline.RunReport)
    assert report.device == ctx.ip
    assert report.errors == {}
    assert len(report.tasks) == len(EXPECTED_ORDER)


@pytest.mark.parametrize(
    ("flag", "task"),
    [
        ("enable_hellgate", "地獄之門"),
        ("enable_arena", "競技場挑戰"),
        ("enable_mining", "挖礦/Oracle"),
        ("enable_cloud_battle", "雲端戰鬥"),
    ],
)
def test_disabled_flags_gate_their_task(patched_pipeline, flag, task):
    ctx, _unused, _device = _build_context(**{flag: False})
    pipeline.run(ctx)

    assert task not in patched_pipeline


def test_due_check_uses_pipeline_start_time(monkeypatch, patched_pipeline):
    seen: list[tuple[str, datetime.datetime]] = []

    def is_due(name, ip, when):
        seen.append((name, when))
        return False

    monkeypatch.setattr(pipeline.task_due, "is_due", is_due)
    ctx, _unused, _device = _build_context(
        current_time=time.struct_time((2026, 8, 9, 22, 0, 0, 5, 221, -1))
    )
    pipeline.run(ctx)

    assert seen == [("地獄之門", datetime.datetime(2026, 8, 9, 22, 0, 0))]
    assert "地獄之門" not in patched_pipeline


def test_ws_done_gates_tasks_without_short_circuiting(patched_pipeline):
    skipped = frozenset({"紅包檢查", "七日登入獎勵", "農場任務", "點擊寶箱",
                         "家族任務", "領取守護靈", "商店購買", "挖礦/Oracle",
                         "所有日常任務", "天梯每週獎勵", "萬神試煉", "雲端戰鬥",
                         "好友每日禮物", "開神燈", "轉盤金幣"})
    ctx, _unused, _device = _build_context(ws_done=skipped)
    pipeline.run(ctx)

    assert not (set(patched_pipeline) & set(skipped))
    assert "坐騎強化" in patched_pipeline


def test_task_4_stage_is_reused_for_guardian_and_skill(monkeypatch, patched_pipeline):
    stages = iter(["主頁面", "主頁面", "主頁面", "主頁面"])
    monkeypatch.setattr(pipeline, "get_stage_with_check", lambda *a, **k: next(stages, "主頁面"))

    # Task 4 若回傳非主頁面，Task 5/6 必須沿用該 stage，而不是各自重查。
    def family_guard(_d, _ip, _model, task_name=None, _reason=None, fn=None, **_kwargs):
        patched_pipeline.append(task_name)
        if fn is not None:
            fn()
        return "家族頁面" if task_name == "家族任務" else "主頁面"

    monkeypatch.setattr(pipeline, "_run_at_main_page", family_guard)
    ctx, _unused, _device = _build_context()
    pipeline.run(ctx)

    assert "領取守護靈" not in patched_pipeline
    assert "抽技能夥伴" not in patched_pipeline


def test_task_18_refreshes_stage_before_lamp(monkeypatch, patched_pipeline):
    get_stage_calls = 0

    def get_stage(*_args, **_kwargs):
        nonlocal get_stage_calls
        get_stage_calls += 1
        # Task 1, store, wanshen, biweekly 的查詢先走主頁；Task 18 完成後
        # 的最後一次查詢回傳新 stage，必須原樣傳給 lamp。
        return "神燈頁面" if get_stage_calls == 5 else "主頁面"

    lamp_stage: list[str] = []
    monkeypatch.setattr(pipeline, "get_stage_with_check", get_stage)
    monkeypatch.setattr(
        pipeline,
        "_run_lamp_if_due",
        lambda _d, _ip, stage: lamp_stage.append(stage),
    )
    ctx, _unused, _device = _build_context()
    pipeline.run(ctx)

    assert lamp_stage == ["神燈頁面"]


def test_5558_skips_guardian_pair_and_restores_skill(monkeypatch, patched_pipeline):
    switched: list[str] = []
    monkeypatch.setattr(pipeline, "switch_skill_h5", lambda _ip, skill: switched.append(skill))
    ctx, _unused, _device = _build_context(ip="emulator-5558")
    pipeline.run(ctx)

    assert "領取守護靈" not in patched_pipeline
    assert "抽技能夥伴" not in patched_pipeline
    assert switched == ["戰士推圖", "騙人用"]


def test_cleanup_for_fc6536d_restores_chrome_and_stops_game(patched_pipeline):
    ctx, _unused, device = _build_context(ip="fc65396d")
    pipeline.run(ctx)

    assert device.events[-2:] == [
        ("app_start", "com.android.chrome"),
        ("app_stop", "com.mxdzz.tw.and"),
    ]


def test_pipeline_under_test_never_binds_real_ledger_or_due_modules():
    """釘住 stub 隔離契約：本檔的 pipeline 不得綁到真實 json_manager／task_due。

    這是回歸守衛。原本 `_stub_module` 用 `sys.modules.setdefault`，若同 session
    有其他測試檔先載入真實模組，stub 就靜默失效，`time_recording` 會把時間戳
    寫進 repo 根目錄的真實裝置 ledger（`fc65396d.json`），且 `task_due.is_due`
    改用當下時間，讓順序斷言隨執行時刻浮動。直接斷言綁定對象，不依賴
    測試執行順序或當天時間。
    """
    assert pipeline.time_recording is _RECORDED_NOOP
    assert pipeline.task_due.is_due is _IS_DUE_STUB
    # sys.modules 已還原：真實模組不該被本檔的 stub 取代。
    for name in ("json_manager", "game_actions.task_due"):
        module = sys.modules.get(name)
        if module is not None:
            assert getattr(module, "__file__", None) is not None, (
                f"{name} 仍是本檔的 stub，會外洩到其他測試檔"
            )

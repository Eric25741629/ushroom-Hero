"""Wiring tests for the ws_token pure-WS backend integration.

Covers the three seams added to run a device over ``ws_token.runner.run_device``
instead of the ADB/Playwright daily pipeline, all gated behind
``use_ws_runner`` (default False → legacy behavior is unchanged):

  1. config_manager: new ``use_ws_runner`` / ``ws_token_spend`` /
     ``ws_token_sweep_list`` fields + ``"ws_token"`` backend whitelist entry.
  2. device_scan_service: ``get_ws_runner_devices`` selects opted-in devices.
  3. runtime_services.ws_runner_service: ``run_ws_device_cycle`` calls
     run_device (not daily_pipeline) and the loop respects pause / force-sleep.

new_main_v2 itself is import-heavy (device_wrapper → cv2, miner, Skill …), so
the WS branch lives in ``runtime_services.ws_runner_service`` as a small,
light-to-import function that new_main_v2.main delegates to. The tests target
that function directly rather than importing new_main_v2.
"""
import json
import sys
import types

import pytest

import config_manager

sys.modules.setdefault("cv2", types.SimpleNamespace())
sys.modules.setdefault(
    "opencc",
    types.SimpleNamespace(
        OpenCC=lambda *args, **kwargs: types.SimpleNamespace(convert=lambda text: text)
    ),
)
sys.modules.setdefault("uiautomator2", types.SimpleNamespace(Device=object))


# ── shared fixtures / helpers (mirror test_device_enabled_gate.py) ───────────

@pytest.fixture
def temp_config(tmp_path, monkeypatch):
    """Point config_manager at a fresh temp file and reset its mtime cache."""
    cfg_path = tmp_path / "bot_config.json"
    cfg_path.write_text(
        json.dumps({"devices": {}, "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_manager, "CONFIG_FILE", str(cfg_path))
    monkeypatch.setattr(config_manager, "_config_cache", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_mtime_ns", None, raising=False)
    monkeypatch.setattr(config_manager, "_config_cache_path", None, raising=False)
    return cfg_path


def _write_devices(cfg_path, devices):
    cfg_path.write_text(
        json.dumps({"devices": devices, "global": {"mode": "master"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_manager._config_cache = None
    config_manager._config_cache_mtime_ns = None


class _NullLogger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


def _import_scan_service():
    # device_scan_service imports `from device import get_adb_devices`; stub the
    # `device` module so the import stays light in test envs.
    if "device" not in sys.modules:
        stub = types.ModuleType("device")
        stub.get_adb_devices = lambda: []
        sys.modules["device"] = stub
    from runtime_services import device_scan_service
    return device_scan_service


def _stub_sleep_modules(monkeypatch):
    """Provide lazy-import stubs for run_ws_device_loop without OCR/CNN deps."""
    startup_stub = types.ModuleType("runtime_services.startup_sleep")
    startup_stub._handle_startup_sleep = lambda ip, lg: None
    sleep_stub = types.ModuleType("runtime_services.sleep_service")
    sleep_stub.run_sleep_cycle = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "runtime_services.startup_sleep", startup_stub)
    monkeypatch.setitem(sys.modules, "runtime_services.sleep_service", sleep_stub)
    return startup_stub, sleep_stub


# ── 1. config: new fields default off + survive round-trip ───────────────────

def test_ws_fields_are_typed_with_off_defaults():
    from config_manager import DeviceConfig, DEFAULT_DEVICE_CONFIG
    for key in ("use_ws_runner", "ws_token_spend", "ws_token_sweep_list",
                "ws_token_mining"):
        assert key in DeviceConfig.__dataclass_fields__
        assert key in DEFAULT_DEVICE_CONFIG
    dc = DeviceConfig()
    assert dc.use_ws_runner is False
    assert dc.ws_token_spend is False
    assert dc.ws_token_sweep_list == []
    assert dc.ws_token_mining is None
    # 2026-06-12: ws_token 子功能預設全開（使用者指示）
    assert DEFAULT_DEVICE_CONFIG["ws_token"]["mining"]["enabled"] is True


def test_from_dict_reads_ws_fields():
    from config_manager import DeviceConfig
    dc = DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_spend": True,
         "ws_token_sweep_list": [[1, 2, 3]],
         "ws_token_mining": {"enabled": True, "max_steps": 35}}
    )
    assert dc.get("use_ws_runner") is True
    assert dc.get("ws_token_spend") is True
    assert dc.get("ws_token_sweep_list") == [[1, 2, 3]]
    assert dc.get("ws_token_mining") == {"enabled": True, "max_steps": 35}


def test_from_dict_defaults_when_keys_absent():
    from config_manager import DeviceConfig
    dc = DeviceConfig.from_dict({"backend": "adb"})
    assert dc.get("use_ws_runner") is False
    assert dc.get("ws_token_spend") is False
    assert dc.get("ws_token_sweep_list") == []
    assert dc.get("ws_token_mining") is None


# ── 1b. backend whitelist keeps "ws_token" (not downgraded to adb) ───────────

def test_update_device_config_keeps_ws_token_backend(temp_config):
    config_manager.update_device_config("ws-001", {"backend": "ws_token"})
    raw = json.loads(temp_config.read_text(encoding="utf-8"))
    assert raw["devices"]["ws-001"]["backend"] == "ws_token"
    assert raw["devices"]["ws-001"]["use_ws_runner"] is True


def test_update_device_config_unknown_backend_downgrades_to_adb(temp_config):
    config_manager.update_device_config("bad-001", {"backend": "nonsense"})
    raw = json.loads(temp_config.read_text(encoding="utf-8"))
    assert raw["devices"]["bad-001"]["backend"] == "adb"


def test_update_device_config_persists_ws_flags(temp_config):
    config_manager.update_device_config(
        "ws-002",
        {"backend": "ws_token", "use_ws_runner": True, "ws_token_spend": 1,
         "ws_token_sweep_list": [[10, 1, 5, 1]]},
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-002"]
    assert raw["use_ws_runner"] is True
    assert raw["ws_token_spend"] is True  # coerced 1 -> True
    assert raw["ws_token_sweep_list"] == [[10, 1, 5, 1]]


def test_update_device_config_persists_ws_mining_config(temp_config):
    config_manager.update_device_config(
        "ws-mine",
        {
            "backend": "ws_token",
            "ws_token_mining": {
                "enabled": 1,
                "allow_bomb": "true",
                "allow_drill": "yes",
                "max_steps": 9999,
                "max_depth": "42",
            },
        },
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-mine"]
    assert raw["ws_token_mining"] == {
        "enabled": True,
        "allow_bomb": True,
        "allow_drill": True,
        "max_steps": 500,
        "max_depth": 42,
    }


def test_update_device_config_bad_ws_mining_becomes_none(temp_config):
    config_manager.update_device_config(
        "ws-mine-bad",
        {"backend": "ws_token", "ws_token_mining": "not-a-dict"},
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-mine-bad"]
    assert raw["ws_token_mining"] is None


def test_update_device_config_sanitizes_bad_sweep_list(temp_config):
    config_manager.update_device_config(
        "ws-003",
        {"backend": "ws_token", "ws_token_sweep_list": ["junk", [1, 2], [3, 4, 5], 7]},
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-003"]
    # "junk"/7 dropped (not lists); [1,2] dropped (<3 entries); [3,4,5] kept.
    assert raw["ws_token_sweep_list"] == [[3, 4, 5]]


def test_update_device_config_non_list_sweep_becomes_empty(temp_config):
    config_manager.update_device_config(
        "ws-004", {"backend": "ws_token", "ws_token_sweep_list": "not-a-list"}
    )
    raw = json.loads(temp_config.read_text(encoding="utf-8"))["devices"]["ws-004"]
    assert raw["ws_token_sweep_list"] == []


# ── 2. scanner: get_ws_runner_devices selection ──────────────────────────────

def test_get_ws_runner_devices_selects_opted_in(temp_config):
    _write_devices(
        temp_config,
        {
            "ws-flag": {"backend": "adb", "use_ws_runner": True},
            "ws-backend": {"backend": "ws_token"},
            "ws-off": {"backend": "ws_token", "enabled": False},
            "web-dev": {"backend": "web_h5"},
            "adb-dev": {"backend": "adb"},
        },
    )
    svc = _import_scan_service()
    result = svc.get_ws_runner_devices(_NullLogger())
    assert "ws-flag" in result        # use_ws_runner=True selects regardless of backend
    assert "ws-backend" in result     # backend=="ws_token" selects too
    assert "ws-off" not in result     # disabled device skipped
    assert "web-dev" not in result
    assert "adb-dev" not in result


def test_is_ws_runner_device_helper(temp_config):
    _write_devices(
        temp_config,
        {
            "ws-flag": {"backend": "adb", "use_ws_runner": True},
            "plain": {"backend": "adb"},
        },
    )
    svc = _import_scan_service()
    assert svc.is_ws_runner_device("ws-flag") is True
    assert svc.is_ws_runner_device("plain") is False


# ── 3. ws_runner_service: run_device dispatch + pause/force-sleep ─────────────

@pytest.fixture
def patched_runner(monkeypatch):
    """Stub bot_state side effects and capture run_device calls on the service."""
    import runtime_services.ws_runner_service as svc

    calls = []

    def fake_run_device(ip, *, spend=False, sweep_list=None, open_lamp=False,
                        lamp_percent=0.0, lamp_min_keep=0, lamp_daily_min=0,
                        farm_config=None, dungeon_sweeps=None, carpark_target=None,
                        carpark_auto=False, carpark_plan=None, couple_gifts=True, forge_ring=False,
                        workshop_rotate=True, kungfu_guess=False,
                        kungfu_worship=False,
                        mail_claim=False, mail_gem_threshold=None,
                        mail_skill_threshold=None,
                        relic_upgrade=False, relic_max_steps=10,
                        relic_fragment_floor=0, tycoon=False, tycoon_max_rolls=50,
                        relic_sprint_enabled=False, relic_sprint_target=900000,
                        gacha_config=None,
                        mining_config=None,
                        sea_config=None,
                        only_tasks=None,
                        cloud_ladder_enabled=False,
                        ladder_reward_enabled=False,
                        progress=None,
                        should_abort=None):
        calls.append({"ip": ip, "spend": spend, "sweep_list": sweep_list,
                      "open_lamp": open_lamp, "lamp_percent": lamp_percent,
                      "lamp_min_keep": lamp_min_keep,
                      "lamp_daily_min": lamp_daily_min, "farm_config": farm_config,
                      "dungeon_sweeps": dungeon_sweeps, "carpark_target": carpark_target,
                      "carpark_auto": carpark_auto,
                      "carpark_plan": carpark_plan,
                      "couple_gifts": couple_gifts, "forge_ring": forge_ring,
                      "workshop_rotate": workshop_rotate,
                      "kungfu_guess": kungfu_guess,
                      "kungfu_worship": kungfu_worship,
                      "mail_claim": mail_claim,
                      "mail_gem_threshold": mail_gem_threshold,
                      "mail_skill_threshold": mail_skill_threshold,
                      "relic_upgrade": relic_upgrade,
                      "relic_max_steps": relic_max_steps,
                      "relic_fragment_floor": relic_fragment_floor,
                      "tycoon": tycoon, "tycoon_max_rolls": tycoon_max_rolls,
                      "relic_sprint_enabled": relic_sprint_enabled,
                      "relic_sprint_target": relic_sprint_target,
                      "gacha_config": gacha_config,
                      "mining_config": mining_config,
                      "sea_config": sea_config,
                      "only_tasks": only_tasks,
                      "cloud_ladder_enabled": cloud_ladder_enabled,
                      "ladder_reward_enabled": ladder_reward_enabled})
        return types.SimpleNamespace(
            device=ip, login_ok=True, spend=spend, tasks={"main_tasks": {}}, errors={}
        )

    # _load_run_device is the lazy seam; patch it so no WS deps are touched.
    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    return svc, calls


def test_run_ws_device_cycle_calls_run_device_with_cfg_flags(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_spend": True, "ws_token_sweep_list": [[1, 2, 3]]}
    )
    report = svc.run_ws_device_cycle("ws-x", cfg, _NullLogger())
    assert len(calls) == 1
    assert calls[0] == {"ip": "ws-x", "spend": True, "sweep_list": [[1, 2, 3]],
                        "open_lamp": False, "lamp_percent": 0.0,
                        "lamp_min_keep": 0, "lamp_daily_min": 0,
                        "farm_config": None,
                        "dungeon_sweeps": None, "carpark_target": None,
                        "carpark_auto": False,
                        "carpark_plan": None,
                        "couple_gifts": True, "forge_ring": False,
                        "workshop_rotate": True, "kungfu_guess": False,
                        "kungfu_worship": False,
                        "mail_claim": False, "mail_gem_threshold": None,
                        "mail_skill_threshold": None,
                        "relic_upgrade": False, "relic_max_steps": 10,
                        "relic_fragment_floor": 0,
                        "tycoon": False, "tycoon_max_rolls": 50,
                        "relic_sprint_enabled": False,
                        "relic_sprint_target": 900000,
                        "gacha_config": None,
                        "mining_config": None,
                        "sea_config": None,
                        "only_tasks": None,
                        "cloud_ladder_enabled": True,
                        "ladder_reward_enabled": True}
    assert report.login_ok is True


def test_run_ws_device_cycle_excludes_5558_weekly_ladder(
        patched_runner, monkeypatch):
    svc, calls = patched_runner
    monkeypatch.setattr(svc, "_protected_player_online", lambda *a, **k: False)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("emulator-5558", cfg, _NullLogger())
    assert calls[0]["cloud_ladder_enabled"] is False
    assert calls[0]["ladder_reward_enabled"] is False


def test_run_ws_device_cycle_passes_kungfu_guess(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_kungfu_guess": True}
    )
    svc.run_ws_device_cycle("ws-kf", cfg, _NullLogger())
    assert calls[0]["kungfu_guess"] is True


def test_run_ws_device_cycle_mail_claim_defaults_false(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-nomail", cfg, _NullLogger())
    assert calls[0]["mail_claim"] is False
    assert calls[0]["mail_gem_threshold"] is None
    assert calls[0]["mail_skill_threshold"] is None


def test_run_ws_device_cycle_reads_mail_claim_from_nested(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"mail_claim": True,
                      "mail_gem_threshold": 500, "mail_skill_threshold": 80}}
    )
    svc.run_ws_device_cycle("ws-mail", cfg, _NullLogger())
    assert calls[0]["mail_claim"] is True
    assert calls[0]["mail_gem_threshold"] == 500
    assert calls[0]["mail_skill_threshold"] == 80


def test_run_ws_device_cycle_kungfu_guess_defaults_false(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-nokf", cfg, _NullLogger())
    assert calls[0]["kungfu_guess"] is False


def test_run_ws_device_cycle_relic_tycoon_default_off(patched_runner):
    """遺物強化 / 傳奇大亨 預設 OFF + bounded（無巢狀設定時走預設上限）。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-no-rt", cfg, _NullLogger())
    assert calls[0]["relic_upgrade"] is False
    assert calls[0]["relic_max_steps"] == 10
    assert calls[0]["relic_fragment_floor"] == 0
    assert calls[0]["tycoon"] is False
    assert calls[0]["tycoon_max_rolls"] == 50


def test_run_ws_device_cycle_reads_relic_tycoon_from_nested(patched_runner):
    """遺物強化 / 傳奇大亨 來自巢狀 ws_token dict（單一真相），coerce 後傳入。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"relic_upgrade": True, "relic_max_steps": 5,
                      "relic_fragment_floor": 200000,
                      "tycoon": True, "tycoon_max_rolls": 12}}
    )
    svc.run_ws_device_cycle("ws-rt", cfg, _NullLogger())
    assert calls[0]["relic_upgrade"] is True
    assert calls[0]["relic_max_steps"] == 5
    assert calls[0]["relic_fragment_floor"] == 200000
    assert calls[0]["tycoon"] is True
    assert calls[0]["tycoon_max_rolls"] == 12


def test_run_ws_device_cycle_relic_sprint_default_off(patched_runner):
    """遺物碎片衝刺 預設 OFF：service 不傳 → fake 收到預設 (False / 900000)。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-no-sprint", cfg, _NullLogger())
    assert calls[0]["relic_sprint_enabled"] is False
    assert calls[0]["relic_sprint_target"] == 900000


def test_run_ws_device_cycle_reads_relic_sprint_from_nested(patched_runner):
    """遺物碎片衝刺 來自巢狀 ws_token.relic_sprint（單一真相），啟用時傳 True + target。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"relic_sprint": {"enabled": True, "target_spend": 450000}}}
    )
    svc.run_ws_device_cycle("ws-sprint", cfg, _NullLogger())
    assert calls[0]["relic_sprint_enabled"] is True
    assert calls[0]["relic_sprint_target"] == 450000


def test_run_ws_device_cycle_relic_sprint_enabled_uses_default_target(patched_runner):
    """啟用但沒給 target_spend → service 不傳 target，run_device 走預設 900000。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"relic_sprint": {"enabled": True}}}
    )
    svc.run_ws_device_cycle("ws-sprint-def", cfg, _NullLogger())
    assert calls[0]["relic_sprint_enabled"] is True
    assert calls[0]["relic_sprint_target"] == 900000


def test_run_ws_device_cycle_reads_gacha_from_nested(patched_runner):
    """抽卡設定來自巢狀 ws_token.gacha（單一真相），coerce 後傳入 run_device。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"gacha": {"enabled": True, "types": [1],
                                "mode": "fixed", "count": 35, "batches": 2}}}
    )
    svc.run_ws_device_cycle("ws-gacha", cfg, _NullLogger())
    g = calls[0]["gacha_config"]
    assert g is not None and g["enabled"] is True
    assert g["types"] == [1] and g["mode"] == "fixed"
    assert g["count"] == 35 and g["batches"] == 2


def test_run_ws_device_cycle_gacha_defaults_off(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-nogacha", cfg, _NullLogger())
    g = calls[0]["gacha_config"]
    assert g is None or g.get("enabled") is False


def test_run_ws_device_cycle_passes_mining_config(patched_runner):
    svc, calls = patched_runner
    mining_config = {"enabled": True, "allow_bomb": True, "max_steps": 12}
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token_mining": mining_config}
    )
    svc.run_ws_device_cycle("ws-mine", cfg, _NullLogger())
    assert calls[0]["mining_config"] == mining_config


def test_run_ws_device_cycle_passes_none_sweep_when_empty(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-y", cfg, _NullLogger())
    # empty list -> None so run_device's "no chapters configured" path triggers.
    assert calls[0]["spend"] is False
    assert calls[0]["sweep_list"] is None


def test_run_ws_device_cycle_login_failure_does_not_raise(patched_runner, monkeypatch):
    svc, calls = patched_runner

    def failing_login(ip, *, spend=False, sweep_list=None, open_lamp=False,
                      lamp_percent=0.0, lamp_min_keep=0, lamp_daily_min=0,
                      farm_config=None, dungeon_sweeps=None, carpark_target=None,
                          carpark_auto=False, carpark_plan=None, couple_gifts=True, forge_ring=False,
                          workshop_rotate=True, kungfu_guess=False,
                          kungfu_worship=False,
                          mail_claim=False, mail_gem_threshold=None,
                      mail_skill_threshold=None,
                      relic_upgrade=False, relic_max_steps=10,
                      relic_fragment_floor=0, tycoon=False, tycoon_max_rolls=50,
                      gacha_config=None,
                      mining_config=None, sea_config=None,
                      only_tasks=None, cloud_ladder_enabled=False,
                      ladder_reward_enabled=False,
                      progress=None, should_abort=None):
        calls.append(ip)
        return types.SimpleNamespace(
            device=ip, login_ok=False, spend=spend, tasks={}, errors={"login": "no ticket"}
        )

    monkeypatch.setattr(svc, "_load_run_device", lambda: failing_login)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    report = svc.run_ws_device_cycle("ws-z", cfg, _NullLogger())
    assert report.login_ok is False  # logged as warning, not raised


def test_run_ws_device_cycle_swallows_run_device_exception(patched_runner, monkeypatch):
    svc, _calls = patched_runner

    def boom(ip, *, spend=False, sweep_list=None, open_lamp=False,
             lamp_percent=0.0, lamp_min_keep=0, lamp_daily_min=0,
             farm_config=None, dungeon_sweeps=None, carpark_target=None,
             carpark_auto=False, carpark_plan=None,
             couple_gifts=True, forge_ring=False, workshop_rotate=True,
             kungfu_guess=False, mail_claim=False, mail_gem_threshold=None,
             mail_skill_threshold=None,
             relic_upgrade=False, relic_max_steps=10, relic_fragment_floor=0,
             tycoon=False, tycoon_max_rolls=50,
             mining_config=None, sea_config=None,
             only_tasks=None, progress=None, should_abort=None):
        raise RuntimeError("ws blew up")

    monkeypatch.setattr(svc, "_load_run_device", lambda: boom)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    # one bad pass must not propagate out of the cycle (thread must survive).
    assert svc.run_ws_device_cycle("ws-e", cfg, _NullLogger()) is None


def test_loop_runs_cycle_then_sleeps_then_stops(monkeypatch):
    """One wake: loop calls run_ws_device_cycle exactly once, then sleeps.

    The sleep stub raises after the first cycle to terminate the otherwise
    infinite loop deterministically — proving the cycle ran before sleep and
    that no ADB/Playwright init was attempted.
    """
    import runtime_services.ws_runner_service as svc

    cycle_calls = []
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, cfg, lg: cycle_calls.append(ip))
    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: False)
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config",
                        lambda ip: config_manager.DeviceConfig.from_dict({"use_ws_runner": True}))

    # Stub the lazily-imported sleep/startup helpers without importing OCR/CNN deps.
    _ss, sl = _stub_sleep_modules(monkeypatch)

    class _Stop(Exception):
        pass

    def fake_sleep(ip, lg, **k):
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    # The loop catches the broad Exception, logs, and returns — so _Stop ends it.
    svc.run_ws_device_loop("ws-loop", _NullLogger())
    assert cycle_calls == ["ws-loop"]  # exactly one cycle before sleep


def test_loop_force_sleep_skips_cycle(monkeypatch):
    """When force-sleep is requested, the cycle (run_device) must NOT run."""
    import runtime_services.ws_runner_service as svc

    cycle_calls = []
    monkeypatch.setattr(svc, "run_ws_device_cycle",
                        lambda ip, cfg, lg: cycle_calls.append(ip))
    monkeypatch.setattr(svc.bot_state, "init_device", lambda ip: None)
    monkeypatch.setattr(svc.bot_state, "set_offline", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    monkeypatch.setattr(svc.bot_state, "check_force_sleep", lambda ip: True)  # force sleep!
    monkeypatch.setattr(svc.bot_state, "check_pause", lambda ip: None)
    monkeypatch.setattr(svc.config_manager, "get_device_config",
                        lambda ip: config_manager.DeviceConfig.from_dict({"use_ws_runner": True}))

    _ss, sl = _stub_sleep_modules(monkeypatch)

    class _Stop(Exception):
        pass

    seen = {"policy": None}

    def fake_sleep(ip, lg, **k):
        seen["policy"] = k.get("sleep_policy")
        raise _Stop()

    monkeypatch.setattr(sl, "run_sleep_cycle", fake_sleep)

    svc.run_ws_device_loop("ws-fs", _NullLogger())
    assert cycle_calls == []                 # force-sleep skipped the run
    assert seen["policy"] == "force_sleep"   # and applied the force-sleep policy


def test_run_ws_device_cycle_passes_nested_carpark_plan(patched_runner):
    svc, calls = patched_runner
    plan = {"enabled": True,
            "day": {"window": ["08:00", "20:00"], "cross": 1, "silver": 5}}
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True, "ws_token": {"carpark_plan": plan}}
    )
    svc.run_ws_device_cycle("ws-plan", cfg, _NullLogger())
    assert calls[0]["carpark_plan"]["enabled"] is True
    assert calls[0]["carpark_plan"]["day"]["cross"] == 1


def test_run_ws_device_cycle_reads_lamp_percent_min_keep_from_nested(patched_runner):
    """開神燈百分比/最低保留來自巢狀 ws_token dict（單一真相），coerce 後傳入。"""
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict(
        {"use_ws_runner": True,
         "ws_token": {"lamp_percent": "1.5", "lamp_min_keep": "500000",
                     "lamp_daily_min": "60"}}
    )
    svc.run_ws_device_cycle("ws-lamp", cfg, _NullLogger())
    assert calls[0]["lamp_percent"] == 1.5
    assert calls[0]["lamp_min_keep"] == 500000
    assert calls[0]["lamp_daily_min"] == 60


def test_run_ws_device_cycle_lamp_defaults_zero_when_absent(patched_runner):
    svc, calls = patched_runner
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-nolamp", cfg, _NullLogger())
    assert calls[0]["lamp_percent"] == 0.0
    assert calls[0]["lamp_min_keep"] == 0
    assert calls[0]["lamp_daily_min"] == 0


def test_progress_branch_maps_lamp_progress_to_step(monkeypatch):
    """_progress(..., 'progress', '12/34') 應把 bot_state step 設成 'WS 開神燈 (12/34)'。"""
    import runtime_services.ws_runner_service as svc

    steps: list[tuple] = []
    monkeypatch.setattr(svc.bot_state, "update_state",
                        lambda ip, **k: steps.append((ip, k.get("step"))))

    captured_progress = {}

    def fake_run_device(ip, *, progress=None, **k):
        captured_progress["fn"] = progress
        return types.SimpleNamespace(
            device=ip, login_ok=True, spend=False, tasks={}, errors={})

    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-prog", cfg, _NullLogger())

    progress = captured_progress["fn"]
    assert callable(progress)
    progress("lamp", "progress", "12/34")
    assert ("ws-prog", "WS 開神燈 (12/34)") in steps


def test_ws_runner_service_forwards_main_chapter_kills(monkeypatch):
    import runtime_services.ws_runner_service as svc

    captured = {}

    def fake_run_device(ip, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            device=ip, login_ok=True, spend=False, tasks={}, errors={})

    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    monkeypatch.setattr(svc.bot_state, "update_state", lambda *a, **k: None)
    cfg = config_manager.DeviceConfig.from_dict({
        "use_ws_runner": True,
        "ws_token": {
            "main_chapter_kills": {
                "enabled": True,
                "interval_sec": 3.0,
                "persist_every": 10,
            }
        },
    })

    svc.run_ws_device_cycle("ws-kills", cfg, _NullLogger())

    assert captured["main_chapter_kills_config"]["enabled"] is True
    assert captured["main_chapter_kills_config"]["interval_sec"] == 3.0


def test_progress_branch_maps_harvest_card_to_chinese_label(monkeypatch):
    """harvest_card tag 應在 dashboard 顯示為「豐收卡」。"""
    import runtime_services.ws_runner_service as svc

    steps: list[tuple] = []
    monkeypatch.setattr(svc.bot_state, "update_state",
                        lambda ip, **k: steps.append((ip, k.get("step"))))

    captured_progress = {}

    def fake_run_device(ip, *, progress=None, **k):
        captured_progress["fn"] = progress
        return types.SimpleNamespace(
            device=ip, login_ok=True, spend=False, tasks={}, errors={})

    monkeypatch.setattr(svc, "_load_run_device", lambda: fake_run_device)
    cfg = config_manager.DeviceConfig.from_dict({"use_ws_runner": True})
    svc.run_ws_device_cycle("ws-prog-card", cfg, _NullLogger())

    progress = captured_progress["fn"]
    progress("harvest_card", "start", "")
    progress("harvest_card", "ok", "")
    assert ("ws-prog-card", "WS 任務執行中: 豐收卡") in steps
    assert ("ws-prog-card", "WS 任務完成: 豐收卡") in steps

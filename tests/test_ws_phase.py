"""game_actions.ws_phase — WS-first 階段與 RunReport→pipeline skip-set 對照。"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("cv2", types.SimpleNamespace())

import config_manager  # noqa: E402
from game_actions import ws_phase  # noqa: E402
from ws_token.runner import RunReport  # noqa: E402


def _cfg(monkeypatch, ws, *, backend="adb"):
    merged_ws = {"bootstrap_token": False}
    merged_ws.update(ws)
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  {"ws_token": merged_ws, "backend": backend}.get(k, d)})())


def _report(tasks, errors=None, login_ok=True):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {})


def test_disabled_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": False})
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_success_tasks_map_to_pipeline_names(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "redpack": {}, "farm": {}, "idle_reward": {}, "guild": {},
        "spirit": {}, "steward": {}, "main_tasks": {}, "couple": {},
        "lamp": {}, "turntable": {}, "mining": {},
    }))
    skips = ws_phase.run_ws_phase("dev")
    assert skips == frozenset({
        "紅包檢查", "點擊寶箱", "家族任務", "領取守護靈", "商店購買",
        "所有日常任務", "好友每日禮物", "開神燈", "轉盤金幣", "挖礦/Oracle",
    })
    # farm 沒配 seed_id → 農場任務不 skip（spec §8）
    assert "農場任務" not in skips


def test_farm_skips_only_with_seed_id(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "farm": {"seed_id": 4001}})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"farm": {}}))
    assert "農場任務" in ws_phase.run_ws_phase("dev")


def test_dungeon_skips_only_with_sweeps_configured(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "dungeon_sweeps": [[2, 100, 3]]})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"dungeon": {}}))
    assert "萬神試煉" in ws_phase.run_ws_phase("dev")
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"dungeon": {}}))
    assert "萬神試煉" not in ws_phase.run_ws_phase("dev")


def test_mining_skipped_result_does_not_skip_oracle(monkeypatch):
    """mine_until 沒挖到（回傳帶 "skipped"）→ ws_phase 不可標完成，保留 ADB 後備。

    死結時 executed 含 1 個 unconfirmed step（非空），但 mine_until 會加 "skipped"
    sentinel；ws_phase._substantive_done 既有慣例會排除帶 "skipped" 的 dict。
    """
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(
        ws_phase, "_run_device",
        lambda ip, cfg, progress=None, **_kw: _report(
            {"mining": {"executed": [{"block_id": 1, "confirmed": False}],
                        "stopped_reason": "unconfirmed",
                        "skipped": "no dig confirmed (stopped=unconfirmed)"}}))
    assert "挖礦/Oracle" not in ws_phase.run_ws_phase("dev")


def test_mining_done_result_skips_oracle(monkeypatch):
    """有挖到 / 鎬子用完（無 "skipped"）→ 視為完成，skip ADB 挖礦。"""
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(
        ws_phase, "_run_device",
        lambda ip, cfg, progress=None, **_kw: _report(
            {"mining": {"executed": [{"block_id": 1, "confirmed": True}],
                        "stopped_reason": "pickaxe_empty"}}))
    assert "挖礦/Oracle" in ws_phase.run_ws_phase("dev")


def test_run_device_passes_mining_config(monkeypatch):
    captured = {}

    def fake_run_device(ip, **kwargs):
        captured["ip"] = ip
        captured.update(kwargs)
        return _report({"mining": {}})

    import ws_token.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_device", fake_run_device)

    cfg = {"enabled": True, "mining": {"enabled": True, "allow_drill": True}}
    ws_phase._run_device("dev", cfg)

    assert captured["ip"] == "dev"
    assert captured["mining_config"] == {"enabled": True, "allow_drill": True}


def test_run_device_passes_lamp_percent_and_min_keep(monkeypatch):
    captured = {}

    def fake_run_device(ip, **kwargs):
        captured.update(kwargs)
        return _report({"lamp": {}})

    import ws_token.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_device", fake_run_device)

    cfg = {"enabled": True, "open_lamp": True,
           "lamp_percent": 1.0, "lamp_min_keep": 500000}
    ws_phase._run_device("dev", cfg)

    assert captured["lamp_percent"] == 1.0
    assert captured["lamp_min_keep"] == 500000


def test_run_device_lamp_knobs_default_zero(monkeypatch):
    captured = {}

    def fake_run_device(ip, **kwargs):
        captured.update(kwargs)
        return _report({})

    import ws_token.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_device", fake_run_device)

    ws_phase._run_device("dev", {"enabled": True})

    assert captured["lamp_percent"] == 0
    assert captured["lamp_min_keep"] == 0


def test_progress_branch_maps_lamp_progress_to_step(monkeypatch):
    """ws_phase 內 _progress(..., 'progress', '12/34') → step 'WS 開神燈 (12/34)'。"""
    _cfg(monkeypatch, {"enabled": True, "open_lamp": True})
    import bot_state
    steps: list[tuple] = []
    monkeypatch.setattr(bot_state, "update_state",
                        lambda ip, **k: steps.append((ip, k.get("step"))))

    captured = {}

    def fake_run_device(ip, cfg, progress=None, **_kw):
        captured["progress"] = progress
        return _report({"lamp": {}})

    monkeypatch.setattr(ws_phase, "_run_device", fake_run_device)
    ws_phase.run_ws_phase("dev")

    progress = captured["progress"]
    assert callable(progress)
    progress("lamp", "progress", "12/34")
    assert ("dev", "WS 開神燈 (12/34)") in steps


def test_run_device_passes_carpark_plan_and_auto(monkeypatch):
    captured = {}

    def fake_run_device(ip, **kwargs):
        captured.update(kwargs)
        return _report({})

    import ws_token.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_device", fake_run_device)

    plan = {"enabled": True,
            "day": {"window": ["08:00", "20:00"], "cross": 1, "silver": 5}}
    ws_phase._run_device("dev", {"enabled": True, "carpark_plan": plan,
                                 "carpark_auto": True})

    assert captured["carpark_plan"] == plan
    assert captured["carpark_auto"] is True


def test_errored_task_not_skipped(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report(
        {"redpack": {}}, errors={"lamp": "WSTimeoutError: x"}))
    skips = ws_phase.run_ws_phase("dev")
    assert "紅包檢查" in skips and "開神燈" not in skips


def test_task_self_skipped_not_mapped(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report(
        {"couple": {"skipped": "no partner"}, "redpack": {}}))
    skips = ws_phase.run_ws_phase("dev")
    assert "好友每日禮物" not in skips and "紅包檢查" in skips


def test_login_failure_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report(
        {}, errors={"login": "boom"}, login_ok=False))
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_any_exception_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    def _boom(ip, cfg, progress=None, **_kw):
        raise RuntimeError("creds missing")
    monkeypatch.setattr(ws_phase, "_run_device", _boom)
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_adb_missing_creds_bootstraps_then_runs(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "bootstrap_token": True})
    from ws_token import bootstrap
    monkeypatch.setattr(bootstrap, "AUTH_DIR", tmp_path)

    refresh_calls = []
    adb_calls = []

    def fake_refresh(ip):
        refresh_calls.append(ip)
        return object()

    def fake_adb(cmd, **kwargs):
        adb_calls.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(bootstrap, "refresh_creds", fake_refresh)
    monkeypatch.setattr(bootstrap.subprocess, "run", fake_adb)
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"redpack": {}}))

    skips = ws_phase.run_ws_phase("dev")

    assert skips == frozenset({"紅包檢查"})
    assert refresh_calls == ["dev"]
    assert adb_calls == [
        ["adb", "-s", "dev", "shell", "am", "force-stop", bootstrap.PACKAGE],
        ["adb", "-s", "dev", "shell", "input", "keyevent", "HOME"],
    ]


def test_adb_bootstrap_refresh_failure_returns_empty(tmp_path, monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "bootstrap_token": True})
    from ws_token import bootstrap
    monkeypatch.setattr(bootstrap, "AUTH_DIR", tmp_path)
    monkeypatch.setattr(bootstrap, "refresh_creds",
                        lambda ip: (_ for _ in ()).throw(RuntimeError("no ticket")))
    monkeypatch.setattr(bootstrap.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0})())
    run_calls = []
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:run_calls.append(ip) or _report({}))

    assert ws_phase.run_ws_phase("dev") == frozenset()
    assert run_calls == []


def test_web_h5_backend_does_not_adb_bootstrap(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "bootstrap_token": True}, backend="web_h5")
    bootstrap_calls = []
    monkeypatch.setattr(ws_phase, "_bootstrap_token",
                        lambda ip, log, force=False: bootstrap_calls.append(force) or True)
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"redpack": {}}))

    assert ws_phase.run_ws_phase("dev") == frozenset({"紅包檢查"})
    assert bootstrap_calls == []


def _patch_time_recording(monkeypatch):
    """攔截 json_manager.time_recording，回傳呼叫紀錄 list[(ip, name)]。"""
    import json_manager
    calls = []
    monkeypatch.setattr(json_manager, "time_recording",
                        lambda ip, name="": calls.append((ip, name)))
    return calls


def test_ws_success_records_daily_keys_for_dashboard(monkeypatch):
    """WS 成功的任務要回寫 JsonDataManager 當日紀錄，dashboard 徽章才會 ✅。"""
    _cfg(monkeypatch, {"enabled": True, "dungeon_sweeps": [[23, 1081, 1]]})
    calls = _patch_time_recording(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "steward": {}, "guild": {}, "mining": {}, "dungeon": {},
    }))
    ws_phase.run_ws_phase("dev")
    assert set(calls) == {
        ("dev", "Store"),          # 商店購買
        ("dev", "donate_family"),  # 家族任務
        ("dev", "挖礦"),           # 挖礦
        ("dev", "萬神試煉"),
    }


def test_ws_errored_or_self_skipped_tasks_not_recorded(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    calls = _patch_time_recording(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report(
        {"steward": {"skipped": "nothing to buy"}, "redpack": {}},
        errors={"guild": "WSTimeoutError"}))
    skips = ws_phase.run_ws_phase("dev")
    assert calls == []          # redpack 成功但 dashboard 沒追蹤該 key
    assert "紅包檢查" in skips  # skip-set 行為不受影響


def test_record_failure_does_not_break_skip_set(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    import json_manager
    monkeypatch.setattr(json_manager, "time_recording",
                        lambda ip, name="": (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"steward": {}}))
    assert "商店購買" in ws_phase.run_ws_phase("dev")


def _patch_progress_marks(monkeypatch):
    """攔截「每日任務」「農場種植」兩個 WS daily-progress 回寫 helper。"""
    mission_calls = []
    farm_calls = []
    monkeypatch.setattr(ws_phase, "_mark_mission_done",
                        lambda ip, log: mission_calls.append(ip))
    monkeypatch.setattr(ws_phase, "_mark_farm_plant_done",
                        lambda ip, log: farm_calls.append(ip))
    return mission_calls, farm_calls


def test_ws_main_tasks_marks_mission_daily(monkeypatch):
    """WS main_tasks 成功 → 寫「每日任務」(mission_timestamp) 徽章紀錄。"""
    _cfg(monkeypatch, {"enabled": True})
    mission_calls, _ = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg, progress=None, **_kw:_report({"main_tasks": {}}))
    ws_phase.run_ws_phase("dev")
    assert mission_calls == ["dev"]


def test_ws_main_tasks_errored_does_not_mark_mission(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    mission_calls, _ = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report(
        {"redpack": {}}, errors={"main_tasks": "WSTimeoutError"}))
    ws_phase.run_ws_phase("dev")
    assert mission_calls == []


def test_ws_ad_seed_marks_farm_plant(monkeypatch):
    """WS ad_rewards 領到農場種子(config15) → 寫「農場種植」(farm_plant_click)。"""
    _cfg(monkeypatch, {"enabled": True})
    _, farm_calls = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "ad_rewards": {"results": {"農場種子廣告": {"name": "農場種子廣告",
                                                "claimed": 2, "stopped": "remaining_zero"}},
                       "total_claimed": 2},
    }))
    ws_phase.run_ws_phase("dev")
    assert farm_calls == ["dev"]


def test_ws_ad_seed_maxed_marks_farm_plant(monkeypatch):
    """config15 今天已領滿(maxed) → 仍算「農場種植」今日完成。"""
    _cfg(monkeypatch, {"enabled": True})
    _, farm_calls = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "ad_rewards": {"results": {"農場種子廣告": {"name": "農場種子廣告",
                                                "skipped": "maxed 2/2"}},
                       "total_claimed": 0},
    }))
    ws_phase.run_ws_phase("dev")
    assert farm_calls == ["dev"]


def test_ws_ad_seed_cooldown_does_not_mark_farm_plant(monkeypatch):
    """config15 還在冷卻、今天尚未領完 → 不可標「農場種植」完成。"""
    _cfg(monkeypatch, {"enabled": True})
    _, farm_calls = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "ad_rewards": {"results": {"農場種子廣告": {"name": "農場種子廣告",
                                                "skipped": "cooldown until 9999999999"}},
                       "total_claimed": 0},
    }))
    ws_phase.run_ws_phase("dev")
    assert farm_calls == []


def test_ws_no_ad_seed_does_not_mark_farm_plant(monkeypatch):
    """ad_rewards 沒含農場種子(只領鑽石) → 不標農場種植。"""
    _cfg(monkeypatch, {"enabled": True})
    _, farm_calls = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "ad_rewards": {"results": {"商城廣告鑽石": {"name": "商城廣告鑽石",
                                                "claimed": 3}},
                       "total_claimed": 3},
    }))
    ws_phase.run_ws_phase("dev")
    assert farm_calls == []


def test_ws_ad_seed_claimed_zero_error_does_not_mark_farm_plant(monkeypatch):
    """config15 claimed=0 + error_code（送了請求但被擋下=失敗/冷卻）→ 今天還沒領完，
    不可標「農場種植」完成。

    這是 codex MEDIUM 的徽章誤標：claim_ad 失敗時仍回 {"claimed": 0, "stopped":
    "error_code=..."}；舊版只要有 claimed key 就算完成 → 把失敗誤標成當日完成。
    """
    _cfg(monkeypatch, {"enabled": True})
    _, farm_calls = _patch_progress_marks(monkeypatch)
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg, progress=None, **_kw:_report({
        "ad_rewards": {"results": {"農場種子廣告": {"name": "農場種子廣告",
                                                "claimed": 0,
                                                "stopped": "error_code=89"}},
                       "total_claimed": 0},
    }))
    ws_phase.run_ws_phase("dev")
    assert farm_calls == []


def test_mark_mission_done_writes_flat_scalar(tmp_path, monkeypatch):
    """_mark_mission_done 寫 flat scalar(不可巢狀化破壞 Mission.py 讀側)。"""
    monkeypatch.chdir(tmp_path)
    import logging
    ws_phase._mark_mission_done("dev123", logging.getLogger("t"))
    from utils.json_io import read_json_bom_safe
    on_disk = read_json_bom_safe("dev123.json")
    assert isinstance(on_disk["mission_timestamp"], (int, float))
    assert not isinstance(on_disk["mission_timestamp"], bool)
    assert on_disk["mission_timestamp"] > 0
    # 不可巢狀化（dashboard / Mission.py 讀 flat）
    assert not isinstance(on_disk["mission_timestamp"], dict)


def test_mark_farm_plant_done_writes_dict_with_count(tmp_path, monkeypatch):
    """_mark_farm_plant_done 寫 dict schema(讀側 is_same_day 才認)。"""
    monkeypatch.chdir(tmp_path)
    import logging
    ws_phase._mark_farm_plant_done("dev456", logging.getLogger("t"))
    from utils.json_io import read_json_bom_safe
    on_disk = read_json_bom_safe("dev456.json")
    rec = on_disk["farm_plant_click"]
    assert isinstance(rec, dict)
    assert "timestamp" in rec and rec["timestamp"] > 0


def test_adb_login_failure_refreshes_once_and_retries(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "bootstrap_token": True})
    bootstrap_calls = []
    monkeypatch.setattr(ws_phase, "_bootstrap_token",
                        lambda ip, log, force=False: bootstrap_calls.append(force) or True)
    reports = iter([
        _report({}, errors={"login": "expired"}, login_ok=False),
        _report({"redpack": {}}),
    ])
    run_calls = []

    def fake_run(ip, cfg, progress=None, **_kw):
        run_calls.append(ip)
        return next(reports)

    monkeypatch.setattr(ws_phase, "_run_device", fake_run)

    assert ws_phase.run_ws_phase("dev") == frozenset({"紅包檢查"})
    assert bootstrap_calls == [False, True]
    assert run_calls == ["dev", "dev"]

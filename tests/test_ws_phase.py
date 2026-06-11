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


def _cfg(monkeypatch, ws):
    monkeypatch.setattr(
        config_manager, "get_device_config",
        lambda ip: type("C", (), {"get": lambda self, k, d=None:
                                  {"ws_token": ws}.get(k, d)})())


def _report(tasks, errors=None, login_ok=True):
    return RunReport(device="dev", login_ok=login_ok, spend=False,
                     tasks=tasks, errors=errors or {})


def test_disabled_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": False})
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_success_tasks_map_to_pipeline_names(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report({
        "redpack": {}, "farm": {}, "idle_reward": {}, "guild": {},
        "spirit": {}, "steward": {}, "main_tasks": {}, "couple": {},
        "lamp": {}, "turntable": {}, "mining": {},
    }))
    skips = ws_phase.run_ws_phase("dev")
    assert skips == frozenset({
        "紅包檢查", "點擊寶箱", "家族任務", "領取守護靈", "商店購買",
        "所有日常任務", "好友每日禮物", "開神燈", "轉盤金幣", "挖礦任務",
    })
    # farm 沒配 seed_id → 農場任務不 skip（spec §8）
    assert "農場任務" not in skips


def test_farm_skips_only_with_seed_id(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "farm": {"seed_id": 4001}})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"farm": {}}))
    assert "農場任務" in ws_phase.run_ws_phase("dev")


def test_dungeon_skips_only_with_sweeps_configured(monkeypatch):
    _cfg(monkeypatch, {"enabled": True, "dungeon_sweeps": [[2, 100, 3]]})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"dungeon": {}}))
    assert "萬神試煉" in ws_phase.run_ws_phase("dev")
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device",
                        lambda ip, cfg: _report({"dungeon": {}}))
    assert "萬神試煉" not in ws_phase.run_ws_phase("dev")


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


def test_errored_task_not_skipped(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report(
        {"redpack": {}}, errors={"lamp": "WSTimeoutError: x"}))
    skips = ws_phase.run_ws_phase("dev")
    assert "紅包檢查" in skips and "開神燈" not in skips


def test_task_self_skipped_not_mapped(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report(
        {"couple": {"skipped": "no partner"}, "redpack": {}}))
    skips = ws_phase.run_ws_phase("dev")
    assert "好友每日禮物" not in skips and "紅包檢查" in skips


def test_login_failure_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    monkeypatch.setattr(ws_phase, "_run_device", lambda ip, cfg: _report(
        {}, errors={"login": "boom"}, login_ok=False))
    assert ws_phase.run_ws_phase("dev") == frozenset()


def test_any_exception_returns_empty(monkeypatch):
    _cfg(monkeypatch, {"enabled": True})
    def _boom(ip, cfg):
        raise RuntimeError("creds missing")
    monkeypatch.setattr(ws_phase, "_run_device", _boom)
    assert ws_phase.run_ws_phase("dev") == frozenset()

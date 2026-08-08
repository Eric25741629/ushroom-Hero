"""Unit tests for Phase D1 skip-browser runtime logic.

涵蓋 game_actions.task_due.any_client_due（enable/due 組合 + fail-safe）與
game_actions.browser_skip.should_skip_browser（每個條件 True/False + fail-safe）。

純函式測試：monkeypatch config / is_due / any_client_due，並以 sys.modules 注入輕量
假 statue_weekly / fannaoxiao_scheduler / dragon_realm，避免 import cv2 / device /
playwright 等重模組。AAA 結構。
"""

import datetime
import sys
import types

import config_manager
from game_actions import browser_skip, task_due

_TPE = datetime.timezone(datetime.timedelta(hours=8))


def _dt(y, m, d, hh=12, mm=0):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=_TPE)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _base_cfg(**overrides):
    """web_h5 裝置，所有 flag 任務預設關閉（純靠 is_due 控制 always-on 任務）。"""
    cfg = {
        "backend": "web_h5",
        "enable_hellgate": False,
        "enable_arena": False,
        "enable_mount_sprint": False,
        "enable_cloud_battle": False,
        "enable_wanshen": False,
        "enable_biweekly": False,
    }
    cfg.update(overrides)
    return cfg


def _patch_cfg(monkeypatch, cfg, *, raises=False):
    def fake_get(ip):
        if raises:
            raise RuntimeError("boom-config")
        return cfg

    monkeypatch.setattr(config_manager, "get_device_config", fake_get)


def _patch_is_due(monkeypatch, due_tasks=(), *, raises_for=None):
    def fake_is_due(task, ip, now=None):
        if raises_for is not None and task == raises_for:
            raise RuntimeError("boom-due")
        return task in due_tasks

    monkeypatch.setattr(task_due, "is_due", fake_is_due)


def _stub_delegated(monkeypatch, *, statue=False, dragon=False,
                    fannaoxiao=False, statue_raises=False):
    """注入 statue_weekly / dragon_realm / fannaoxiao_scheduler 輕量假模組。"""
    sm = types.ModuleType("game_actions.statue_weekly")

    def _statue_enabled(cfg):
        if statue_raises:
            raise RuntimeError("boom-statue")
        return statue

    sm._is_enabled = _statue_enabled
    monkeypatch.setitem(sys.modules, "game_actions.statue_weekly", sm)

    dm = types.ModuleType("dragon_realm")
    dm.use_dragon_realm = lambda ip, config: dragon
    monkeypatch.setitem(sys.modules, "dragon_realm", dm)
    # _en_dragon 會呼叫 config_manager.load_config()（結果被假 use_dragon_realm 忽略）
    monkeypatch.setattr(config_manager, "load_config", lambda: {}, raising=False)

    fm = types.ModuleType("game_actions.fannaoxiao_scheduler")
    fm._is_enabled = lambda ip: fannaoxiao
    monkeypatch.setitem(sys.modules, "game_actions.fannaoxiao_scheduler", fm)


# --------------------------------------------------------------------------
# any_client_due — enable/due 組合
# --------------------------------------------------------------------------
def test_any_client_due_false_when_nothing_due(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=())
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is False


def test_any_client_due_keeps_browser_for_due_escort(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg(enable_escort=True))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("賞金之路",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 11, 11)) is True


def test_any_client_due_true_when_always_on_task_due(monkeypatch):
    # 航海 無 enable flag（恆啟用）→ 只要 is_due 說 due 就該做
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("航海",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is True


def test_any_client_due_true_when_flag_enabled_and_due(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg(enable_hellgate=True))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("地獄之門",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is True


def test_any_client_due_false_when_flag_disabled_even_if_due(monkeypatch):
    # 地獄之門 due 但 enable_hellgate=False → 不算數
    _patch_cfg(monkeypatch, _base_cfg(enable_hellgate=False))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("地獄之門",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is False


def test_biweekly_not_enabled_on_wrong_device(monkeypatch):
    # enable_biweekly=True 但 ip 非 emulator-5556 → 裝置範圍外，不算數
    _patch_cfg(monkeypatch, _base_cfg(enable_biweekly=True))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("雙週副本",))
    assert task_due.any_client_due("other-device", now=_dt(2026, 7, 6)) is False


def test_biweekly_enabled_on_5556(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg(enable_biweekly=True))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("雙週副本",))
    assert task_due.any_client_due("emulator-5556", now=_dt(2026, 7, 6)) is True


def test_statue_delegated_enabled_and_due(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch, statue=True)
    _patch_is_due(monkeypatch, due_tasks=("菇菇雕像每週",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 10)) is True


def test_statue_delegated_disabled_even_if_due(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch, statue=False)
    _patch_is_due(monkeypatch, due_tasks=("菇菇雕像每週",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 10)) is False


def test_ladder_disabled_on_adb_backend(monkeypatch):
    # 天梯每週獎勵 enable = backend==web_h5；adb → 不算數
    _patch_cfg(monkeypatch, _base_cfg(backend="adb"))
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=("天梯每週獎勵",))
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 7)) is False


# --------------------------------------------------------------------------
# any_client_due — fail-safe（raise → 保守當 due → True）
# --------------------------------------------------------------------------
def test_fail_safe_when_config_read_raises(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg(), raises=True)
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=())
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is True


def test_fail_safe_when_is_due_raises(monkeypatch):
    # 航海 恆啟用；is_due 對它 raise → 保守當 due → True
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch)
    _patch_is_due(monkeypatch, due_tasks=(), raises_for="航海")
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is True


def test_fail_safe_when_enable_raises(monkeypatch):
    # statue enable predicate raise（無其他任務 due）→ 保守當 due → True
    _patch_cfg(monkeypatch, _base_cfg())
    _stub_delegated(monkeypatch, statue_raises=True)
    _patch_is_due(monkeypatch, due_tasks=())
    assert task_due.any_client_due("ip", now=_dt(2026, 7, 6)) is True


# --------------------------------------------------------------------------
# should_skip_browser — 每個條件 True/False
# --------------------------------------------------------------------------
def _skip_cfg(*, backend="web_h5", toggle=True, ws_enabled=True,
              only_tasks=None):
    return {
        "backend": backend,
        "skip_browser_when_all_done": toggle,
        "ws_token": {"enabled": ws_enabled, "only_tasks": only_tasks},
    }


def _patch_any_client_due(monkeypatch, value):
    monkeypatch.setattr(task_due, "any_client_due", lambda ip, now=None: value)


def test_skip_true_when_all_conditions_met(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg())
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is True


def test_skip_false_for_h5_primary_ws_allowlist(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg(only_tasks=["sea_season"]))
    _patch_any_client_due(monkeypatch, False)

    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False


def test_skip_false_when_toggle_off(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg(toggle=False))
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False


def test_skip_false_when_not_web_h5(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg(backend="adb"))
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False


def test_skip_false_when_ws_not_enabled(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg(ws_enabled=False))
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False


def test_skip_false_when_ws_login_not_ok(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg())
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=False) is False


def test_skip_false_when_a_task_is_due(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg())
    _patch_any_client_due(monkeypatch, True)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False


def test_skip_false_when_config_read_raises(monkeypatch):
    _patch_cfg(monkeypatch, _skip_cfg(), raises=True)
    _patch_any_client_due(monkeypatch, False)
    assert browser_skip.should_skip_browser("ip", ws_login_ok=True) is False

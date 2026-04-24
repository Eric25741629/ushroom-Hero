"""Characterization tests for game_actions.manager_factory (_init_runtime_managers).

Phase 9: extract per-device manager creation from main() into its own module.

Tests written BEFORE extraction (expected RED until manager_factory.py exists).

  _init_runtime_managers(d, ip, Cnn_model, oralce_cnn_model, oralce_classes):
    - Returns a 6-tuple of manager objects
    - Creates miner/rl_logs/{ip} directory as a side effect
    - IP containing a colon → colon replaced with underscore in directory name
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Heavy-dep stubs (applied at collection time, before any import of the SUT)
# ---------------------------------------------------------------------------
for _name in ("opencc", "paddleocr", "img_tools", "easyocr"):
    if _name not in sys.modules:
        _m = types.ModuleType(_name)
        if _name == "opencc":
            _m.OpenCC = lambda *a, **kw: SimpleNamespace(convert=lambda s: s)
        sys.modules[_name] = _m

if "uiautomator2" not in sys.modules:
    _u2 = types.ModuleType("uiautomator2")
    _u2.Device = object
    sys.modules["uiautomator2"] = _u2

# Spin_Wheel / Mission / family / State each import device.device or
# uiautomator2 transitively. Stub the whole module so we never pull in
# hardware drivers — the constructor is replaced via monkeypatch anyway.
if "Spin_Wheel" not in sys.modules:
    _sw = types.ModuleType("Spin_Wheel")
    class _FakeSpinWheel:
        def __init__(self, **kw): self._kw = kw
    _sw.spin_wheel = _FakeSpinWheel
    sys.modules["Spin_Wheel"] = _sw

if "Mission" not in sys.modules:
    _mis = types.ModuleType("Mission")
    class _FakeMission:
        def __init__(self, **kw): self._kw = kw
    _mis.mission = _FakeMission
    sys.modules["Mission"] = _mis

if "family" not in sys.modules:
    _fam = types.ModuleType("family")
    class _FakeFamilyManager:
        def __init__(self, **kw): self._kw = kw
    _fam.Family_manager = _FakeFamilyManager
    sys.modules["family"] = _fam

if "State" not in sys.modules:
    _stt = types.ModuleType("State")
    class _FakeState:
        def __init__(self, **kw): self._kw = kw
    _stt.state = _FakeState
    sys.modules["State"] = _stt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def factory_mod():
    import importlib
    return importlib.import_module("game_actions.manager_factory")


@pytest.fixture
def patched_clf(monkeypatch, factory_mod):
    """Replace ClassifierCNN to prevent __post_init__ from calling model.to()."""
    monkeypatch.setattr(
        factory_mod, "ClassifierCNN",
        lambda **kw: SimpleNamespace(_type="clf", kw=kw),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_init_runtime_managers_returns_six_tuple(factory_mod, patched_clf):
    """Normal path: 6-tuple returned — one object per manager type."""
    result = factory_mod._init_runtime_managers(
        SimpleNamespace(), "emulator-5554", SimpleNamespace(), SimpleNamespace(), []
    )
    assert len(result) == 6


def test_init_runtime_managers_creates_rl_dir(factory_mod, patched_clf, tmp_path, monkeypatch):
    """RL logs directory is created at miner/rl_logs/{ip}."""
    monkeypatch.chdir(tmp_path)
    factory_mod._init_runtime_managers(
        SimpleNamespace(), "emulator-5554", SimpleNamespace(), SimpleNamespace(), []
    )
    assert (tmp_path / "miner" / "rl_logs" / "emulator-5554").is_dir()


def test_init_runtime_managers_colon_ip_becomes_underscore_in_dir(
    factory_mod, patched_clf, tmp_path, monkeypatch
):
    """IP with colon → underscore in the rl_logs directory name."""
    monkeypatch.chdir(tmp_path)
    factory_mod._init_runtime_managers(
        SimpleNamespace(), "192.168.1.1:5555", SimpleNamespace(), SimpleNamespace(), []
    )
    assert (tmp_path / "miner" / "rl_logs" / "192.168.1.1_5555").is_dir()

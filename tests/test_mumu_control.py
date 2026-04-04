import logging
import subprocess

import pytest

from utils.mumu_control import (
    MuMuController,
    build_control_args,
    discover_control_exe,
    resolve_emulator_index,
)


def test_resolve_emulator_index():
    assert resolve_emulator_index("emulator-5554") == 0
    assert resolve_emulator_index("emulator-5556") == 1
    assert resolve_emulator_index("emulator-5558") == 2
    assert resolve_emulator_index("emulator-5554", overrides={"emulator-5554": 9}) == 9

    with pytest.raises(ValueError):
        resolve_emulator_index("127.0.0.1:16384")


@pytest.mark.parametrize(
    "action, expected",
    [
        ("launch", ["control", "-v", "0", "launch"]),
        ("shutdown", ["control", "-v", "0", "shutdown"]),
        ("restart", ["control", "-v", "0", "restart"]),
        ("show_window", ["control", "-v", "0", "show_window"]),
        ("hide_window", ["control", "-v", "0", "hide_window"]),
    ],
)
def test_build_commands(action, expected):
    assert build_control_args(0, action) == expected


def test_run_action_timeout_returns_failed_result():
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 20))

    controller = MuMuController("MuMuManager.exe", runner=_raise_timeout)
    result = controller.restart("emulator-5554", timeout_sec=1)

    assert result.ok is False
    assert result.returncode == -1
    assert "Timeout" in result.stderr


def test_discover_mumu_manager_only_path(monkeypatch):
    manager_path = r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe"

    def _fake_exists(path):
        return path == manager_path

    monkeypatch.setattr("utils.mumu_control.os.path.exists", _fake_exists)
    assert discover_control_exe() == manager_path


def test_manager_path_actions_keep_index_mapping():
    calls = []

    def _runner(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

    controller = MuMuController(r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe", runner=_runner)

    controller.launch("emulator-5554")
    controller.shutdown("emulator-5556")
    controller.restart("emulator-5558")

    assert calls[0][-4:] == ["control", "-v", "0", "launch"]
    assert calls[1][-4:] == ["control", "-v", "1", "shutdown"]
    assert calls[2][-4:] == ["control", "-v", "2", "restart"]


def test_logs_selected_mumu_manager_path(caplog):
    caplog.set_level(logging.INFO)
    path = r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe"
    MuMuController(path, runner=lambda *args, **kwargs: subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr=""))
    assert any("MuMu executable selected" in rec.message and "MuMuManager.exe" in rec.message for rec in caplog.records)


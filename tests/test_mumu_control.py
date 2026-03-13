import subprocess

import pytest

from utils.mumu_control import MuMuController, build_control_args, resolve_emulator_index


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
        ("launch", ["-v", "0", "launch"]),
        ("shutdown", ["-v", "0", "shutdown"]),
        ("restart", ["-v", "0", "restart"]),
        ("show_window", ["-v", "0", "show_window"]),
        ("hide_window", ["-v", "0", "hide_window"]),
    ],
)
def test_build_commands(action, expected):
    assert build_control_args(0, action) == expected


def test_run_action_timeout_returns_failed_result():
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 20))

    controller = MuMuController("control.exe", runner=_raise_timeout)
    result = controller.restart("emulator-5554", timeout_sec=1)

    assert result.ok is False
    assert result.returncode == -1
    assert "Timeout" in result.stderr

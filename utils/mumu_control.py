import os
import re
import subprocess
import time
import logging
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional


# emulator-<port> format, e.g. emulator-5554
_EMULATOR_RE = re.compile(r"^emulator-(\d+)$")


def resolve_emulator_index(serial: str, overrides: Optional[Dict[str, int]] = None) -> int:
    """Map emulator serial to MuMu index.

    Default rule:
    emulator-5554 -> 0
    emulator-5556 -> 1
    emulator-5558 -> 2
    """
    if overrides and serial in overrides:
        return int(overrides[serial])

    match = _EMULATOR_RE.match(serial or "")
    if not match:
        raise ValueError(f"Unsupported emulator serial: {serial}")

    port = int(match.group(1))
    if port < 5554 or (port - 5554) % 2 != 0:
        raise ValueError(f"Invalid emulator port mapping: {serial}")
    return (port - 5554) // 2


def build_control_args(index: int, action: str) -> list[str]:
    """Build MuMu control args."""
    valid_actions = {"launch", "shutdown", "restart", "show_window", "hide_window"}
    if action not in valid_actions:
        raise ValueError(f"Unsupported action: {action}")
    return ["control", "-v", str(int(index)), action]


def discover_control_exe(candidates: Optional[Iterable[str]] = None) -> Optional[str]:
    """Find MuMu control executable.

    UAT confirmed this environment uses MuMuManager.exe as the only valid control entry.
    """
    if candidates is None:
        candidates = [
            r"C:\Program Files\Netease\MuMuPlayer\nx_main\MuMuManager.exe",
        ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


@dataclass
class ControlResult:
    ok: bool
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration_sec: float


class MuMuController:
    """Wrapper for MuMu executable operations."""

    def __init__(
        self,
        control_exe_path: str,
        serial_to_index_overrides: Optional[Dict[str, int]] = None,
        runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self.control_exe_path = control_exe_path
        self.serial_to_index_overrides = serial_to_index_overrides or {}
        self._runner = runner or subprocess.run
        self.logger.info("MuMu executable selected: %s", self.control_exe_path)

    def resolve_index(self, serial: str) -> int:
        return resolve_emulator_index(serial, self.serial_to_index_overrides)

    def run_action(self, serial: str, action: str, timeout_sec: int = 20) -> ControlResult:
        index = self.resolve_index(serial)
        args = [self.control_exe_path] + build_control_args(index, action)
        started = time.time()
        self.logger.info("MuMu control action=%s serial=%s index=%s", action, serial, index)

        try:
            proc = self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            duration = time.time() - started
        except subprocess.TimeoutExpired as exc:
            duration = time.time() - started
            return ControlResult(
                ok=False,
                command=" ".join(args),
                returncode=-1,
                stdout="",
                stderr=f"Timeout after {timeout_sec}s: {exc}",
                duration_sec=duration,
            )
        except FileNotFoundError as exc:
            duration = time.time() - started
            return ControlResult(
                ok=False,
                command=" ".join(args),
                returncode=-2,
                stdout="",
                stderr=str(exc),
                duration_sec=duration,
            )

        return ControlResult(
            ok=(proc.returncode == 0),
            command=" ".join(args),
            returncode=int(proc.returncode),
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            duration_sec=duration,
        )

    def launch(self, serial: str, timeout_sec: int = 20) -> ControlResult:
        return self.run_action(serial, "launch", timeout_sec=timeout_sec)

    def shutdown(self, serial: str, timeout_sec: int = 20) -> ControlResult:
        return self.run_action(serial, "shutdown", timeout_sec=timeout_sec)

    def restart(self, serial: str, timeout_sec: int = 20) -> ControlResult:
        return self.run_action(serial, "restart", timeout_sec=timeout_sec)

    def show_window(self, serial: str, timeout_sec: int = 20) -> ControlResult:
        return self.run_action(serial, "show_window", timeout_sec=timeout_sec)

    def hide_window(self, serial: str, timeout_sec: int = 20) -> ControlResult:
        return self.run_action(serial, "hide_window", timeout_sec=timeout_sec)


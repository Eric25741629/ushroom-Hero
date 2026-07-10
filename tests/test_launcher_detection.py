"""is_on_launcher 桌面偵測：優先用 dumpsys window mCurrentFocus，不信 app_current fallback。

背景（2026-07-10, adb-fc65396d 小米實機）：Android 11+ 的 `dumpsys window windows`
不再輸出 mCurrentFocus，adbutils app_current() 會 fallback 到 `dumpsys activity top`
取 recents 最後一筆，人在桌面時卻回傳遊戲 package，造成「未在桌面」誤判迴圈。
"""
import logging
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.modules.setdefault("uiautomator2", MagicMock())

import adb_operations  # noqa: E402

logger = logging.getLogger("test")

LAUNCHER_FOCUS = (
    "  mCurrentFocus=Window{8b9f7e9 u0 com.mi.android.globallauncher/com.miui.home.launcher.Launcher}\n"
    "  mFocusedApp=ActivityRecord{8b9f7e9 u0 com.mi.android.globallauncher/com.miui.home.launcher.Launcher t123}\n"
)
GAME_FOCUS = (
    "  mCurrentFocus=Window{829ff63 u999 com.mxdzz.tw.and/com.cocos.game.AppActivity}\n"
)
KEYGUARD_FOCUS = "  mCurrentFocus=Window{c0ffee u0 NotificationShade}\n"


class FakeDevice:
    """d.shell() 回傳帶 .output 的物件（u2 ShellResponse 形狀）。"""

    serial = "fake-serial"

    def __init__(self, shell_output="", shell_exc=None, app_current_pkg=None):
        self._shell_output = shell_output
        self._shell_exc = shell_exc
        self._app_current_pkg = app_current_pkg

    def shell(self, cmd):
        if self._shell_exc:
            raise self._shell_exc
        return types.SimpleNamespace(output=self._shell_output, exit_code=0)

    def app_current(self):
        if self._app_current_pkg is None:
            raise RuntimeError("no app_current")
        return {"package": self._app_current_pkg, "activity": "x"}


def test_focus_package_parses_launcher():
    d = FakeDevice(shell_output=LAUNCHER_FOCUS)
    assert adb_operations._current_focus_package(d) == "com.mi.android.globallauncher"


def test_focus_package_parses_multiuser_game():
    d = FakeDevice(shell_output=GAME_FOCUS)
    assert adb_operations._current_focus_package(d) == "com.mxdzz.tw.and"


def test_focus_package_keyguard_returns_none():
    # NotificationShade 沒有 package/activity 形式 → 無法判定
    d = FakeDevice(shell_output=KEYGUARD_FOCUS)
    assert adb_operations._current_focus_package(d) is None


def test_is_on_launcher_true_when_focus_is_launcher():
    d = FakeDevice(shell_output=LAUNCHER_FOCUS, app_current_pkg="com.mxdzz.tw.and")
    # app_current 回傳遊戲（fallback 誤報情境），但 focus 是桌面 → 必須信 focus
    assert adb_operations.is_on_launcher(d, logger) is True


def test_is_on_launcher_false_when_focus_is_game():
    d = FakeDevice(shell_output=GAME_FOCUS, app_current_pkg="com.mi.android.globallauncher")
    assert adb_operations.is_on_launcher(d, logger) is False


def test_is_on_launcher_falls_back_to_app_current():
    # dumpsys 沒有 mCurrentFocus（或 shell 失敗）→ 退回 app_current
    d = FakeDevice(shell_output="", app_current_pkg="com.miui.home")
    assert adb_operations.is_on_launcher(d, logger) is True

    d2 = FakeDevice(shell_exc=RuntimeError("adb broken"), app_current_pkg="com.mxdzz.tw.and")
    assert adb_operations.is_on_launcher(d2, logger) is False

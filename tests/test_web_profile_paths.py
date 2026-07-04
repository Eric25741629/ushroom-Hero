"""Characterization tests for the shared web profile/state path resolver (cx-5).

Pins the behavior that device_wrapper._start and control_panel.routes_web_session
now share, so the dashboard manual-login profile and the runtime profile always
resolve to the SAME normpathed directory.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.web_profile_paths import resolve_profile_dir, resolve_state_file  # noqa: E402


def test_profile_dir_default_is_absolute_normpathed_and_per_device():
    p = resolve_profile_dir("emu-1", None)
    assert os.path.isabs(p)
    assert p == os.path.normpath(p)          # canonical (no mixed separators)
    assert os.path.basename(p) == "emu-1"    # per-device, never shared


def test_profile_dir_placeholder_substituted():
    assert os.path.basename(resolve_profile_dir("dev9", "playwright_profile/{device_id}")) == "dev9"


def test_profile_dir_without_placeholder_appends_device_id():
    p = resolve_profile_dir("dev9", "myprofiles")
    assert os.path.basename(p) == "dev9"
    assert "myprofiles" in p


def test_state_file_default():
    s = resolve_state_file("dev9", None)
    assert os.path.isabs(s)
    assert s == os.path.normpath(s)
    assert os.path.basename(s) == "dev9.json"
    assert os.path.basename(os.path.dirname(s)) == "auth_state"


def test_state_file_placeholder():
    assert os.path.basename(resolve_state_file("dev9", "auth_state/{device_id}.json")) == "dev9.json"


def test_state_file_bare_auth_state_json_gets_per_device_name():
    s = resolve_state_file("dev9", "some/dir/auth_state.json")
    assert os.path.basename(s) == "dev9.json"
    assert os.path.basename(os.path.dirname(s)) == "auth_state"


def test_deterministic():
    assert resolve_profile_dir("d", None) == resolve_profile_dir("d", None)
    assert resolve_state_file("d", None) == resolve_state_file("d", None)


def test_empty_configured_falls_back_to_default():
    # blank / whitespace configured value must not produce an empty path
    assert os.path.basename(resolve_profile_dir("d", "   ")) == "d"
    assert os.path.basename(resolve_state_file("d", "   ")) == "d.json"

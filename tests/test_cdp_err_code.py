"""Tests for the shared CDP error -> HTTP status mapping (cx-1 dedup)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from control_panel.shared.cdp import _cdp_err_code  # noqa: E402


def test_no_web_debug_port_is_400():
    assert _cdp_err_code("no web_debug_port") == 400


def test_no_cdp_target_is_502():
    assert _cdp_err_code("no CDP target on port 9230") == 502


def test_other_errors_are_500():
    assert _cdp_err_code("timeout") == 500
    assert _cdp_err_code("Connection refused") == 500

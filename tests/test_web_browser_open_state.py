"""bot_state.set_web_browser_open publishes the device-thread's authoritative
"web_h5 browser currently open?" reading into per-device state so the dashboard
/api/status can reconcile the 開啟/關閉網頁 button with reality (root-cause fix
for the button staying on 關閉網頁 after an external/manual browser close).

is_alive() is thread-affine (device-thread only), so the truth must be published
from the device thread; these tests cover the publish setter + snapshot read.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot_state  # noqa: E402


def test_set_web_browser_open_published_in_snapshot():
    ip = "test-webopen-pub"
    bot_state.update_state(ip, task="t", step="s")  # create the state row
    bot_state.set_web_browser_open(ip, True)
    assert bot_state.get_all_states()[ip]["web_browser_open"] is True
    bot_state.set_web_browser_open(ip, False)
    assert bot_state.get_all_states()[ip]["web_browser_open"] is False


def test_set_web_browser_open_is_noop_without_existing_state():
    # A device with no state row yet must NOT be conjured into existence by a
    # publish (it gets one on its next update_state); the call is a safe no-op.
    ip = "test-webopen-never-seen"
    bot_state.set_web_browser_open(ip, True)
    assert ip not in bot_state.get_all_states()


def test_set_web_browser_open_coerces_to_bool():
    ip = "test-webopen-coerce"
    bot_state.update_state(ip, task="t", step="s")
    bot_state.set_web_browser_open(ip, 1)
    assert bot_state.get_all_states()[ip]["web_browser_open"] is True

"""dashboard.html step countdown must count down from an absolute deadline.

Regression (2026-06-05): the old logic relabelled "執行中 (2400s)" as "等待中"
and derived remaining seconds from the step-string digits minus `last_update`.
Because background heartbeats refresh `last_update`, the number froze (~2400) and
rendered as an ugly float (2399.8699922561646s). The fix counts down from
`info.step_deadline` (a fixed epoch) using the client clock, rounded to whole
seconds.
"""
import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def _html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_step_countdown_uses_step_deadline():
    html = _html()
    assert "info.step_deadline" in html
    # remaining is rounded to an integer second (no raw float like 2399.8699s)
    assert "Math.round(info.step_deadline" in html


def test_broken_last_update_subtraction_countdown_removed():
    html = _html()
    # the old fragile patterns are gone
    assert "等待中(" not in html
    assert r"match(/\((\d+)s\)/)" not in html
    assert "Math.floor(Date.now() / 1000) - (info.last_update" not in html


def test_dashboard_has_device_enable_toggle():
    """A disabled device must surface an enable control on its card.

    New devices register disabled; the dashboard reads `info.enabled` from the
    status payload and offers a toggle wired to the per-device config endpoint.
    """
    html = _html()
    assert "info.enabled" in html
    assert "toggleDeviceEnabled" in html


def test_disabled_web_device_opens_web_via_login_worker():
    """A disabled web_h5 device must still be able to open the browser for
    login/setup before it is enabled.

    The normal "開啟網頁" button posts to /api/web_launch, which only writes the
    `_web_launch_requests` mailbox; every consumer of that mailbox runs inside the
    per-device automation thread. A disabled device is skipped by the scanner and
    has no thread, so a web_launch request is never consumed. The disabled card
    therefore routes through the standalone login worker (/api/web_login), the same
    path device registration uses, which drives its own Playwright session.
    """
    html = _html()
    assert "openWebForSetup" in html
    start = html.index("function openWebForSetup")
    body = html[start:start + 700]
    assert "/api/web_login/" in body
    assert "/api/web_launch/" not in body


def test_opengold_v2_dead_flag_toggle_removed():
    """The lamp always routes to opengold_v2 (lamp_scheduler ignores the flag), so
    the settings checkbox that toggled `use_opengold_v2` is dead UI and must be gone.
    """
    html = _html()
    assert "chkOpenGoldV2" not in html
    assert "use_opengold_v2" not in html


def _fly_pet_nav_tag(html: str) -> str:
    """Return the opening <a ...> tag of the 飛寵管理 side-rail entry."""
    match = re.search(r'<a[^>]*href="/fly-pet"[^>]*>', html)
    assert match, "fly-pet nav entry not found in dashboard"
    return match.group(0)


def test_fly_pet_nav_item_consistent_with_buttons():
    """飛寵管理 must render like the other .nav-btn items.

    Regression (2026-06-05): the fly-pet link was an <a> carrying inline
    `display:flex; align-items:center; color:inherit`, while every other nav
    item is a bare <button class="nav-btn">. content-box + overflow:hidden
    clipped its right edge and color:inherit gave it the body colour
    (--text-primary #e0e0e0) instead of the buttons' #ddd, so it looked off.
    The anchor should now rely entirely on the shared class.
    """
    tag = _fly_pet_nav_tag(_html())
    assert 'class="nav-btn"' in tag
    # no divergent inline overrides that made it look different from buttons
    assert "color:inherit" not in tag
    assert "display:flex" not in tag
    assert "align-items" not in tag


def test_nav_btn_class_pins_deterministic_box_model():
    """.nav-btn must pin border-box so width:100%+padding never overflows/clips.

    Without it, only the <button> elements get the form-control border-box
    default; the <a> fly-pet entry stayed content-box and overflowed the rail.
    """
    match = re.search(r"\.nav-btn\s*\{(.*?)\}", _html(), re.S)
    assert match, ".nav-btn rule not found"
    rule = match.group(1)
    assert "box-sizing: border-box" in rule
    # the class also kills the anchor underline so no inline override is needed
    assert "text-decoration: none" in rule

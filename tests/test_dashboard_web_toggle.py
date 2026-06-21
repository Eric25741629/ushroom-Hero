"""Dashboard 的「開啟網頁」按鈕要能 toggle 成「關閉網頁」,且點擊後鎖 3 秒防連點。

按「開啟網頁」→ 變「關閉網頁」(POST /api/web_launch);按「關閉網頁」→ 變「開啟網頁」
(POST /api/web_close)。3 秒鎖避免連點重複送請求。

按鈕狀態以**後端權威值** `info.web_browser_open`(device thread 發布 is_alive())對帳:
client-side 樂觀 flag (`_webOpenState`/`_webOpenTs`) 只當點擊瞬間的即時回饋 + grace
window,下一輪 status 一到就以真值校正 → 外部手動關閉瀏覽器後按鈕會自動翻回「開啟網頁」。
測試為純字串比對(與 test_dashboard_template.py 同風格)。
"""
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"


def _html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_close_web_page_function_exists():
    html = _html()
    assert "function closeWebPage" in html
    assert "/api/web_close/" in html


def test_open_button_toggles_to_close_label():
    html = _html()
    # 兩種標籤都要在 render 出現(依狀態擇一)
    assert "開啟網頁" in html
    assert "關閉網頁" in html


def test_launch_still_posts_web_launch():
    html = _html()
    assert "/api/web_launch/" in html


def test_optimistic_toggle_state_tracked_per_ip():
    html = _html()
    assert "_webOpenState" in html


def test_toggle_reconciles_with_authoritative_browser_state():
    # The button must derive from the backend-published truth, not only the
    # optimistic flag, so an external/manual close flips it back to 開啟網頁.
    html = _html()
    assert "web_browser_open" in html
    # a grace timestamp lets the optimistic "open" survive the launch gap
    assert "_webOpenTs" in html


def test_three_second_click_lock():
    html = _html()
    assert "webBtnLocked" in html
    assert "lockWebBtn" in html
    # 鎖定時長 3 秒
    assert "3000" in html


def test_web_buttons_tagged_for_lock_targeting():
    html = _html()
    assert "data-webbtn" in html

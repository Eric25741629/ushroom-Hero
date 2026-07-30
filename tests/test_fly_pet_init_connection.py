"""飛寵頁純 WS 連線生命週期契約。"""
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"


def _t() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _fn_body(html: str, marker: str) -> str:
    start = html.index(marker)
    end = html.index("\n}\n", start)
    return html[start:end]


def test_dom_ready_checks_existing_session_without_auto_login():
    html = _t()
    listener = html[html.index("DOMContentLoaded"):]
    assert "initAutoLoad()" in listener
    body = _fn_body(html, "async function initAutoLoad")
    assert "checkBrowserUp(" in body
    assert "connectFlyPetWs(" not in body


def test_connection_check_uses_pure_ws_endpoint():
    body = _fn_body(_t(), "async function checkBrowserUp")
    assert "/api/fly_pet_check_connection/" in body
    assert "/api/fly_pet_browser_status/" not in body


def test_load_connects_pure_ws_without_web_launch():
    html = _t()
    load = _fn_body(html, "async function doLoad")
    assert "connectFlyPetWs()" in load
    assert "/api/web_launch/" not in html
    assert '>啟動瀏覽器</button>' not in html


def test_pure_ws_session_has_keepalive_and_unload_release():
    html = _t()
    assert "/api/ws_session/" in html
    assert "/ping" in html
    assert "beforeunload" in html
    assert "sendBeacon" in html
    assert "/disconnect" in html


def test_unconnected_hint_points_to_load_without_second_connect_button():
    body = _fn_body(_t(), "function showNotConnectedHint")
    assert "不需啟動瀏覽器" in body
    assert "launchBtn" not in body
    assert "點「載入」建立純 WS 連線" in body
    assert "'err'" not in body


def test_btn_attention_style_exists():
    assert ".btn-attention" in _t()

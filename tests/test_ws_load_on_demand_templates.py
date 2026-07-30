"""管理分頁只能由使用者按下讀取按鈕後建立純 WS session。"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(name: str) -> str:
    return (ROOT / "templates" / name).read_text(encoding="utf-8-sig")


def _function(html: str, name: str, next_name: str) -> str:
    start = html.index(f"async function {name}(")
    brace = html.index("{", start)
    depth = 0
    for pos in range(brace, len(html)):
        if html[pos] == "{":
            depth += 1
        elif html[pos] == "}":
            depth -= 1
            if depth == 0:
                return html[start:pos + 1]
    raise AssertionError(f"找不到 {name} 的函式結尾")


def test_fly_pet_has_only_load_entry_for_ws_connect():
    html = _text("fly_pet.html")
    body = _function(html, "doLoad", "doSell")
    assert 'id="launchBtn"' not in html
    assert "async function doLaunch(" not in html
    assert "await connectFlyPetWs()" in body


def test_inventory_read_buttons_connect_on_demand_only():
    html = _text("inventory.html")
    spirit = _function(html, "loadSpirit", "renderSpirit")
    gem = _function(html, "loadGem", "renderGem")
    change = _function(html, "onDevChange", "pingOnce")
    assert 'id="connBtn"' not in html
    assert "function toggleConn(" not in html
    assert "await ensureConnected()" in spirit
    assert "await ensureConnected()" in gem
    assert "await connectSession()" not in change
    assert "reconnectFromKick" not in html
    assert "else { connectSession(); }" not in html


def test_tools_read_actions_connect_on_demand_only():
    html = _text("tools_optimize.html")
    plan = _function(html, "doPlan", "doExecute")
    relic = _function(html, "doRsPlan", "doRsExec")
    dragon = _function(html, "doDrRead", "doDrRun")
    change = _function(html, "onDevChange", "ensureConnected")
    assert 'id="connBtn"' not in html
    assert "function toggleConn(" not in html
    assert "await ensureConnected()" in plan
    assert "await ensureConnected()" in relic
    assert "await ensureConnected()" in dragon
    assert "await connectSession()" not in change
    assert 'id="btnGLoad"' in html
    assert "async function loadGacha(" in html
    assert "請先按抽卡區的「載入」" in html
    assert "reconnectFromKick" not in html


def test_dashboard_relic_settings_do_not_open_ws_implicitly():
    html = _text("dashboard.html")
    assert "loadRelicSprintDate()" not in html
    assert "/api/relic_sprint/plan/" not in html


def test_star_seize_waits_for_load_and_uses_ws_session():
    html = _text("star_seize.html")
    change = _function(html, "onDevChange", "loadPage")
    assert 'id="loadBtn"' in html
    assert "/api/ws_session/" in html
    assert "/connect?label=" in html
    assert "await connectSession()" not in change
    assert "await connectSession()" in _function(html, "loadPage", "stopTimers")

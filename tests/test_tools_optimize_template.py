"""tools_optimize.html 必備 hooks 測試（mirror test_inventory_template.py）。

純字串斷言，不啟瀏覽器、不碰 device：守住「工具 優化類」面板各工具
（車位裝飾、純 WS 抽卡、遺物衝刺）的關鍵 id / 函式 / API 端點，以及與
神器附魔面板同一套的純 WS 連線/閒置/踢線 UI，避免日後改版誤刪。
"""
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "templates" / "tools_optimize.html"


def _html() -> str:
    return TOOLS.read_text(encoding="utf-8")


def test_tools_optimize_has_device_selector():
    html = _html()
    assert 'id="dev"' in html
    assert "loadDevices" in html
    assert "/api/status" in html


def test_carpark_tool_hooks_intact():
    html = _html()
    assert "doPlan()" in html
    assert "doExecute()" in html
    assert "/api/carpark/plan/" in html
    assert "/api/carpark/execute/" in html
    assert "/api/carpark/job/" in html


def test_gacha_tool_hooks_intact():
    html = _html()
    assert "doDraw()" in html
    assert "/api/gacha/draw/" in html
    assert 'id="gType"' in html and 'id="gMode"' in html


def test_relic_sprint_section_hooks():
    html = _html()
    # 兩階段 規劃 → 確認執行 按鈕與函式
    assert "doRsPlan()" in html
    assert "doRsExec()" in html
    # API 端點接上
    assert "/api/relic_sprint/plan/" in html
    assert "/api/relic_sprint/execute/" in html
    # 進度卡片容器 + 4 輪渲染
    assert 'id="rsCard"' in html
    assert 'id="rsRounds"' in html


def test_ws_session_connection_ui_mirrors_inventory():
    html = _html()
    # 連線狀態列 + 連線/斷線按鈕
    assert 'id="connState"' in html
    assert "toggleConn()" in html
    # 純 WS session 三端點
    assert "/api/ws_session/" in html
    assert "/connect" in html and "/ping" in html and "/disconnect" in html
    # 心跳保活 + 動作前確保連線
    assert "startKeepalive" in html
    assert "ensureConnected" in html


def test_idle_and_kick_modals_present():
    html = _html()
    assert 'id="idleModal"' in html
    assert 'id="kickModal"' in html
    assert "beginIdleCountdown" in html
    assert "onKicked" in html
    # 關窗即時釋放連線（恢復 bot loop），sweeper 為安全網
    assert "beforeunload" in html
    assert "sendBeacon" in html

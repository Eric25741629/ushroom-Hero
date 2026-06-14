"""inventory.html 必備 hooks 測試（mirror test_dashboard_template.py）。

純字串斷言，不啟瀏覽器、不碰 device：守住前端關鍵 id / 函式 / 過濾器 / 品質色變數
與兩個分頁按鈕，避免日後改版誤刪。
"""
from pathlib import Path

INVENTORY = Path(__file__).resolve().parents[1] / "templates" / "inventory.html"


def _html() -> str:
    return INVENTORY.read_text(encoding="utf-8")


def test_inventory_has_device_selector():
    html = _html()
    assert 'id="dev"' in html
    # devices loaded from /api/status (web_h5 only)
    assert "loadDevices" in html
    assert "/api/status" in html


def test_inventory_has_two_tab_buttons():
    html = _html()
    assert 'id="tabSpirit"' in html
    assert 'id="tabGem"' in html
    assert "showTab('spirit')" in html
    assert "showTab('gem')" in html


def test_inventory_spirit_section_hooks():
    html = _html()
    assert 'id="secSpirit"' in html
    assert "loadSpirit()" in html
    assert "renderSpirit" in html
    # 詞條過濾 input
    assert 'id="spAffix"' in html
    # spirit API endpoint wired
    assert "/api/spirit_list/" in html


def test_inventory_gem_section_hooks():
    html = _html()
    assert 'id="secGem"' in html
    assert "loadGem()" in html
    assert "renderGem" in html
    # 屬性過濾 input ("聯合搜索" client-side filter)
    assert 'id="gAttr"' in html
    # 賣最低候選 + 分解 actions
    assert "selectSellCandidates()" in html
    assert "doDecompose()" in html
    # gem API endpoints wired (read + pending action)
    assert "/api/artifact_gem_list/" in html
    assert "/api/artifact_gem_action/" in html


def test_inventory_quality_color_vars():
    html = _html()
    # quality palette --q3..--q8 defined and mapped in QCOLORS
    for q in range(3, 9):
        assert f"--q{q}" in html, f"missing --q{q}"
    assert "QCOLORS" in html

from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"
CONTROL_PANEL = Path(__file__).resolve().parents[1] / "control_panel_app.py"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _control_panel_text() -> str:
    return CONTROL_PANEL.read_text(encoding="utf-8")


def test_breed_refresh_and_hatch_use_egg_protocols():
    """培育基地刷新要同步蛋列表；孵化必須送 egg id 到 send_66_3。

    send_66_29 是改繁殖場名稱，不是孵化。
    """
    source = _control_panel_text()

    assert "send_66_1()" in source
    assert "egg_id = data.get(\"egg_id\")" in source
    assert "egg_ids_js = json.dumps([int(egg_id)])" in source
    assert "normalEvent.on('EggListBack', handler);" in source
    assert "send_66_3({egg_ids_js})" in source
    assert "send_66_29({int(base_id)})" not in source


def test_breed_template_hatches_eggs_by_egg_id():
    """前端蛋列表的孵化按鈕要傳 egg_id，不是巢穴 base_id。"""
    html = _template_text()
    hatch_start = html.index("async function doHatch")
    hatch_end = html.index("async function discoverMethods", hatch_start)
    hatch_body = html[hatch_start:hatch_end]

    assert "doHatch(' + Number(eg.id || 0) + ')" in html
    assert "{egg_id: eggId}" in hatch_body
    assert "base_id" not in hatch_body


def test_auto_breed_tick_has_overlap_guard():
    """自動繁殖輪詢是 async；必須避免上一輪未結束時重疊送操作封包。"""
    html = _template_text()

    assert "var abTicking = false;" in html
    assert "if (abTicking) return;" in html
    assert "abTicking = true;" in html
    assert "abTicking = false;" in html


def test_manual_load_breed_refreshes_before_reading_cache():
    """手動載入繁殖池不能只讀舊 cache，需先請遊戲端刷新蛋/繁殖場資料。"""
    html = _template_text()
    load_start = html.index("async function loadBreed")
    load_end = html.index("async function doHatch", load_start)
    load_body = html[load_start:load_end]

    refresh_pos = load_body.index("/api/fly_pet_refresh_breed/")
    info_pos = load_body.index("/api/fly_pet_breed_info/")
    assert refresh_pos < info_pos

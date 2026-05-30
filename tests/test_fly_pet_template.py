from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"
CONTROL_PANEL = Path(__file__).resolve().parents[1] / "control_panel_app.py"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _control_panel_text() -> str:
    return CONTROL_PANEL.read_text(encoding="utf-8")


def _style_block_for(selector: str, text: str) -> str:
    start = text.index(selector)
    return text[start : text.index("}", start) + 1]


def test_fly_pet_table_has_entry_quality_sorting():
    html = _template_text()

    assert "doSort('entry_quality')" in html
    assert 'id="sort-entry_quality"' in html
    assert "function bestPositiveRank" in html
    assert "key === 'entry_quality'" in html
    assert "doSort('entry_level')" not in html
    assert "function bestEntryLevel" not in html


def test_entry_quality_rank_excludes_mutant_work_and_negative():
    """變異(6)/工作(7) 是獨立標記、負面(4) 是缺點 -- 都不列入品質排名。
    只有正向品質 史詩(3)>卓越(2)>稀有(5)>普通(1) 參與排名。"""
    html = _template_text()

    assert "POSITIVE_QUALITY_RANK" in html
    assert "ENTRY_QUALITY_RANK" not in html
    # positive qualities only
    assert "3: 40" in html
    assert "2: 30" in html
    assert "5: 20" in html
    assert "1: 10" in html
    # mutant/work/negative must NOT carry a quality rank anymore
    assert "6: 0" not in html
    assert "7: -1" not in html
    assert "4: 5" not in html


def test_entry_quality_sort_is_default_descending():
    html = _template_text()

    assert "var sortCol = 'entry_quality', sortAsc = false;" in html


def test_epic_entry_chip_uses_visible_background():
    html = _template_text()
    epic_style = _style_block_for(".ec-3 {", html)

    assert "background-clip: text" not in epic_style
    assert "-webkit-text-fill-color: transparent" not in epic_style
    assert "background:" in epic_style
    assert "border:" in epic_style


def test_entry_chip_is_color_only_without_quality_text():
    """詞條 chip 只顯示名稱 + 顏色,品質文字小字移除(tooltip 仍帶品質)。"""
    html = _template_text()

    # the <small> quality label and its style are gone everywhere
    assert "</small>" not in html
    assert ".ec small" not in html
    # chip still rendered by ecHTML, name escaped
    assert "function ecHTML" in html
    assert "esc(e.name) + '</span>'" in html


def test_entries_sorted_by_quality_within_row():
    """每列的詞條依品質顯示順序排序,普通不會排在史詩前面。"""
    html = _template_text()

    assert "QUALITY_DISPLAY_ORDER" in html
    assert "function sortedEntries" in html
    # renderTable must use the sorted view, not the raw entries array
    assert "sortedEntries(p)" in html


def test_entry_quality_headline_excludes_negative_and_shows_dash_when_none():
    """詞條品質頭條只顯示正向品質;沒有正向品質時顯示 --,絕不顯示負面當頭條。"""
    html = _template_text()

    assert "function bestPositiveQuality" in html
    assert "entry-quality-none" in html
    assert ".entry-quality-none" in html  # css rule exists


def test_mutant_and_work_render_as_independent_tags():
    """變異(6)/工作(7) 以獨立標記呈現於詞條品質欄。"""
    html = _template_text()

    assert "TAG_QUALITIES" in html
    assert "function petTags" in html
    assert "eq-tag eq-tag-" in html  # rendered markup
    assert ".eq-tag-6" in html        # css for 變異
    assert ".eq-tag-7" in html        # css for 工作


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

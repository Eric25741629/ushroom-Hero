"""飛寵列表重設計 (Version B 卡片牆 + Version C 左側種類欄) 的契約測試。

string-grep style: 直接讀 templates/fly_pet.html 原始碼,斷言子字串存在/不存在。
這份測試獨立守住整個 redesign:
  - 新 UI 契約 (petIcon / 真圖 endpoint / 種類欄 / 視圖切換 / 視窗化 / 單一搜尋 / 種類篩選)
  - 被保留的共用 helper / 連線 / 自動繁殖 / 孵化 / breed-load 順序 (MUST present)
  - 已移除的「詞條品質」欄相關符號 (MUST NOT appear)
"""
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"


def _t() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _style_block_for(selector: str, text: str) -> str:
    start = text.index(selector)
    return text[start : text.index("}", start) + 1]


# ----------------------------------------------------------------------------
# NEW UI contracts
# ----------------------------------------------------------------------------

def test_pet_icon_function_present():
    """卡片/種類欄都靠 petIcon() 畫圖標 (B 版移植)。"""
    html = _t()
    assert "function petIcon" in html


def test_real_ingame_icon_endpoint_referenced():
    """圖標槽要嘗試載入真實遊戲圖示 /api/fly_pet_icon/<config_id>,失敗才退回佔位頭像。"""
    html = _t()
    assert "/api/fly_pet_icon/" in html
    # endpoint 由 pet.config_id 組出 (slot 設計成日後只換 <img src> 即可)
    assert "config_id" in html


def test_icon_slot_degrades_gracefully_on_404():
    """真圖 endpoint 404 時 <img> onerror 要露出佔位頭像,不可破圖。"""
    html = _t()
    assert "onerror" in html


def test_species_pane_container_present():
    """左側種類欄容器存在 (C 版移植)。"""
    html = _t()
    assert "species-pane" in html
    # 全部種類列 + 種類索引清單容器
    assert "全部種類" in html
    assert "species-list" in html


def test_flat_group_view_toggle_present():
    """平鋪 <-> 依種類分組 視圖切換存在,並與種類欄收合一致。"""
    html = _t()
    assert "data-view" in html
    assert "平鋪" in html
    assert "依種類" in html


def test_intersection_observer_windowing_present():
    """卡片牆 60/批 視窗化 (IntersectionObserver + sentinel)。"""
    html = _t()
    assert "IntersectionObserver" in html
    assert "sentinel" in html


def test_single_search_input_present():
    """單一搜尋框,同時比對種類名與詞條名。"""
    html = _t()
    assert 'id="gallerySearch"' in html
    assert "搜尋種類" in html


def test_species_filtering_present():
    """點種類列把卡片牆篩到該 config_id;'全部種類' 還原。"""
    html = _t()
    assert "data-sp" in html
    assert "flypet-gallery" in html  # 全新 markup 都包在 scope 容器


def test_card_render_uses_sorted_entries_and_ecHTML():
    """卡片詞條 chip 用 sortedEntries(p).map(ecHTML) 渲染 (沿用既有 helper)。"""
    html = _t()
    assert "function ecHTML" in html
    assert "function sortedEntries" in html
    assert "sortedEntries(p)" in html


def test_render_table_alias_retained():
    """保留 renderTable() 名稱,讓任何既有 caller 仍能驅動新渲染。"""
    html = _t()
    assert "function renderTable" in html


def test_per_pet_breed_buttons_reachable():
    """卡片詳情抽屜要能呼叫 setBreedPet(基底/A/B),繁殖區依賴它。"""
    html = _t()
    assert "setBreedPet(" in html


def test_bulk_and_single_dissolve_reachable():
    """新 UI 仍可呼叫 doSell()/doSellOne()。"""
    html = _t()
    assert "doSell(" in html
    assert "doSellOne(" in html


# ----------------------------------------------------------------------------
# MUST remain present (preserved helpers / connection / auto-breed / hatch)
# ----------------------------------------------------------------------------

def test_positive_quality_rank_literals_present():
    html = _t()
    assert "POSITIVE_QUALITY_RANK" in html
    assert "3: 40" in html
    assert "2: 30" in html
    assert "5: 20" in html
    assert "1: 10" in html


def test_quality_display_order_and_sort_defaults():
    html = _t()
    assert "QUALITY_DISPLAY_ORDER" in html
    assert "key === 'entry_quality'" in html
    assert "var sortCol = 'entry_quality', sortAsc = false;" in html
    assert "function bestPositiveRank" in html


def test_ecHTML_tail_unchanged():
    html = _t()
    assert "esc(e.name) + '</span>'" in html


def test_epic_chip_visible_background():
    html = _t()
    epic = _style_block_for(".ec-3 {", html)
    assert "background:" in epic
    assert "border:" in epic
    assert "background-clip: text" not in epic
    assert "-webkit-text-fill-color: transparent" not in epic


def test_connection_logic_preserved():
    html = _t()
    assert "initAutoLoad()" in html
    assert "async function initAutoLoad" in html
    assert "async function checkBrowserUp" in html
    assert "/api/fly_pet_browser_status/" in html
    assert "function showNotConnectedHint" in html
    assert "showNotConnectedHint('launch')" in html
    assert "showNotConnectedHint('loading')" in html
    assert "啟動瀏覽器" in html
    assert "btn-attention" in html
    assert ".btn-attention" in html


def test_auto_breed_overlap_guard_preserved():
    html = _t()
    assert "var abTicking = false;" in html
    assert "if (abTicking) return;" in html
    assert "abTicking = true;" in html
    assert "abTicking = false;" in html


def test_hatch_uses_egg_id():
    html = _t()
    assert "doHatch(' + Number(eg.id || 0) + ')" in html
    assert "{egg_id: eggId}" in html


def test_breed_load_refreshes_before_breed_info():
    html = _t()
    load_start = html.index("async function loadBreed")
    load_end = html.index("async function doHatch", load_start)
    load_body = html[load_start:load_end]
    refresh_pos = load_body.index("/api/fly_pet_refresh_breed/")
    info_pos = load_body.index("/api/fly_pet_breed_info/")
    assert refresh_pos < info_pos


# ----------------------------------------------------------------------------
# MUST NOT appear (removed 詞條品質 column)
# ----------------------------------------------------------------------------

def test_removed_entry_quality_column_symbols_absent():
    html = _t()
    assert "doSort('entry_quality')" not in html
    assert 'id="sort-entry_quality"' not in html
    assert "entry-quality-cell" not in html
    assert "function entryQualityHTML" not in html
    assert "entry-quality-pill" not in html
    assert "eq-tag" not in html
    assert "</small>" not in html
    assert ".ec small" not in html
    assert "ENTRY_QUALITY_RANK" not in html
    assert "toast('遊戲未連線" not in html

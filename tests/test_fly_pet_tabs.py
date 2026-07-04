"""飛寵頁「常駐頁籤分區 + 批量選取加入收藏」契約測試。

string-grep style:直接讀 templates/fly_pet.html 原始碼,斷言子字串存在。
鏡像 tests/test_fly_pet_groups.py / test_fly_pet_template.py 的 _t() 讀法。

守住使用者硬性要求:
  - A 案頁籤 (role=tablist/tab/tabpanel + aria-selected),四分區 1:1 對應。
  - 「選單不能隱藏」:頁籤條 position:sticky + 不透明底 + 高於內容的 z-index,
    內容捲到它底下而它永不隱藏。
  - 批量選取加入收藏:#batchGroupSel + data-batch-group-add + batchAddSelectedToGroup。
  - 上次頁籤持久化:localStorage 'flypetTab_' + ip()。
  - 既有圖鑑/收藏/批次分解功能不被破壞 (回歸)。
"""
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"


def _t() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _between(text: str, start_marker: str, end_marker: str) -> str:
    """取 [start_marker, end_marker) 之間的子字串 (index-window)。"""
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


# ----------------------------------------------------------------------------
# A 案頁籤 — ARIA tablist + 四分區 tabpanel (1:1 對應)
# ----------------------------------------------------------------------------

def test_tablist_present():
    """常駐頁籤條是 ARIA tablist,有可辨識的 label。"""
    html = _t()
    assert 'role="tablist"' in html
    assert 'aria-label="飛寵功能區"' in html
    assert 'id="fpTabBar"' in html


def test_four_tab_buttons_present():
    """四顆頁籤 (圖鑑/配種/手選收藏/搭檔) 各有 id + role=tab + aria-controls。"""
    html = _t()
    for key in ("gallery", "breed", "groups", "partners"):
        assert 'id="fptab-' + key + '"' in html
        assert 'aria-controls="fppanel-' + key + '"' in html


def test_four_tabpanels_present():
    """四個分區各為 tabpanel + 對應 id + aria-labelledby 回指頁籤。"""
    html = _t()
    for key in ("gallery", "breed", "groups", "partners"):
        assert 'id="fppanel-' + key + '"' in html
        assert 'aria-labelledby="fptab-' + key + '"' in html
    # tab-panel + tabpanel 角色都套在 section 上
    assert 'class="section tab-panel is-active" role="tabpanel" id="fppanel-gallery"' in html
    assert 'class="section tab-panel" role="tabpanel" id="fppanel-breed"' in html
    assert 'class="section tab-panel" role="tabpanel" id="fppanel-groups"' in html
    assert 'class="section tab-panel" role="tabpanel" id="fppanel-partners"' in html


def test_default_tab_is_gallery():
    """首次預設選 gallery:該頁籤 aria-selected=true、其 panel is-active。"""
    html = _t()
    assert 'id="fptab-gallery" aria-controls="fppanel-gallery" aria-selected="true"' in html
    assert 'tab-panel is-active" role="tabpanel" id="fppanel-gallery"' in html


# ----------------------------------------------------------------------------
# 「選單不能隱藏」— 常駐 sticky 頁籤條 (不透明底 + 高 z-index)
# ----------------------------------------------------------------------------

def test_tabnav_is_sticky_and_never_hidden():
    """.fp-tabnav:position:sticky + 不透明底 + z-index → 內容捲到底下而選單永不隱藏。"""
    html = _t()
    block = _between(html, ".fp-tabnav {", "}")
    assert "position: sticky" in block
    assert "top: var(--fp-topbar-h" in block       # 黏在 sticky topbar 正下方
    assert "z-index: 50" in block                  # 高於內容
    assert "background: var(--color-surface)" in block  # 不透明底 (內容不透出)


def test_topbar_height_synced_to_css_var():
    """JS 量測 topbar 高度寫入 --fp-topbar-h,load + resize 都更新。"""
    html = _t()
    assert "function fpSyncTopbarHeight" in html
    assert "--fp-topbar-h" in html
    assert "addEventListener('resize', fpSyncTopbarHeight)" in html


def test_panels_have_scroll_margin_for_sticky_bar():
    """tabpanel 加 scroll-margin-top,讓鍵盤聚焦內容不被常駐頁籤條遮住。"""
    html = _t()
    assert "#content .tab-panel { scroll-margin-top:" in html


# ----------------------------------------------------------------------------
# 頁籤切換 JS — roving tabindex + 方向鍵 + 切回 gallery 重渲染
# ----------------------------------------------------------------------------

def test_tab_switch_keyboard_and_roving_tabindex():
    """selectTab/initTabs:click + Enter/Space + 方向鍵 + Home/End,roving tabindex。"""
    html = _t()
    assert "function selectTab" in html
    assert "function initTabs" in html
    init_block = _between(html, "function initTabs", "function restoreTab")
    assert "ArrowRight" in init_block
    assert "ArrowLeft" in init_block
    assert "'Home'" in init_block
    assert "'End'" in init_block
    # roving tabindex:選中顆 0、其餘 -1
    sel_block = _between(html, "function selectTab", "function initTabs")
    assert "tabIndex = on ? 0 : -1" in sel_block


def test_switch_to_gallery_rerenders():
    """切回 gallery 要重渲染 (視窗化觀察器從 display:none 切回不會自己補卡)。"""
    html = _t()
    sel_block = _between(html, "function selectTab", "function initTabs")
    assert "key === 'gallery'" in sel_block
    assert "renderTable()" in sel_block


def test_tab_persisted_in_localstorage():
    """上次頁籤存 localStorage('flypetTab_'+ip()),開頁 restoreTab() 還原。"""
    html = _t()
    assert "localStorage.setItem('flypetTab_' + ip()" in html
    assert "localStorage.getItem('flypetTab_' + ip())" in html
    assert "function restoreTab" in html
    # 預設值 gallery
    restore_block = _between(html, "function restoreTab", "// ---- Init ----")
    assert "'gallery'" in restore_block


def test_inits_wired_on_dom_ready():
    """DOMContentLoaded 內呼叫 initTabs + restoreTab。"""
    html = _t()
    init_block = _between(html, "document.addEventListener('DOMContentLoaded'", "});")
    assert "initTabs();" in init_block
    assert "restoreTab();" in init_block


# ----------------------------------------------------------------------------
# 批量選取加入收藏 — #batchGroupSel + data-batch-group-add + batchAddSelectedToGroup
# ----------------------------------------------------------------------------

def test_batch_group_selector_in_batchbar():
    """批次列含收藏選單 (有 label) + 「加入收藏」鈕,沿用 lib .btn--ghost。"""
    html = _t()
    bar = _between(html, 'id="galleryBatchbar"', "</div>\n      </div>")
    assert 'id="batchGroupSel"' in bar
    assert 'aria-label="選擇要加入的收藏"' in bar
    assert "data-batch-group-add" in bar
    assert "btn btn--ghost btn-sm" in bar
    # 保留既有批次動作 (分解/取消)
    assert 'id="galleryBatchDissolve"' in bar
    assert 'id="galleryBatchClear"' in bar


def test_batch_selector_has_new_collection_option():
    """批次選單末尾有「＋ 新收藏」哨兵選項。"""
    html = _t()
    assert "function refreshBatchGroupSel" in html
    block = _between(html, "function refreshBatchGroupSel", "function batchAddSelectedToGroup")
    assert "__new__" in block
    assert "＋ 新收藏" in block


def test_batch_add_wiring_and_logic():
    """batchAddSelectedToGroup:選 __new__ 先建,own 成員去重加入,toast 回報,清空多選。"""
    html = _t()
    block = _between(html, "function batchAddSelectedToGroup",
                     "function pickableMembersOfSelectedGroup")
    assert "groupNew()" in block                 # 選新收藏 → 先建
    assert "addToGroup(gid, { src: 'own', id: id })" in block  # 透過 addToGroup (內建去重+saveGroups)
    assert "renderGroupList()" in block
    assert "refreshBreedGroupSel()" in block
    assert "已加入" in block                      # toast 文案
    assert "selected.clear()" in block           # 加完清空多選


def test_batch_add_button_event_bound():
    """[data-batch-group-add] 點擊接到 batchAddSelectedToGroup。"""
    html = _t()
    init_block = _between(html, "function initGallery", "function resetGalleryFilters")
    assert "[data-batch-group-add]" in init_block
    assert "batchAddSelectedToGroup()" in init_block


def test_batch_selector_refreshed_with_breed_selector():
    """批次選單與繁殖選單同源刷新:refreshBreedGroupSel 連帶呼叫 refreshBatchGroupSel。"""
    html = _t()
    block = _between(html, "function refreshBreedGroupSel", "function refreshBatchGroupSel")
    assert "refreshBatchGroupSel();" in block


# ----------------------------------------------------------------------------
# 回歸 — 既有圖鑑 / 收藏 / 分區內容未被破壞
# ----------------------------------------------------------------------------

def test_existing_gallery_and_groups_preserved():
    """圖鑑卡片牆、＋收藏入口、收藏面板、搭檔載入鈕、配種表單群組整合都還在。"""
    html = _t()
    assert 'id="flypetGallery"' in html
    assert 'id="galleryStage"' in html
    assert "data-group-add" in html              # 卡片/詳情 ＋收藏
    assert 'id="groupList"' in html
    assert 'id="loadFriendsBtn"' in html
    assert 'id="breedGroupSel"' in html
    assert "function batchAddSelectedToGroup" in html

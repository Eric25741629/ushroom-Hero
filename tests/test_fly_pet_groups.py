"""飛寵「手選收藏群組」功能契約測試 (FLYPET_GROUPS_DESIGN §6)。

string-grep style: 直接讀 templates/fly_pet.html 原始碼,斷言子字串存在。
鏡像 tests/test_fly_pet_gallery.py 的 _t() 讀法。

守住:
  - makeDeviceStore 工廠 (折疊三份 localStorage 樣板 → 三 consumer 共用)
  - flypetGroups 資料層 (load/save/genGroupId/CRUD/STALE/partnerPetCache)
  - 收藏管理面板「手選飛寵收藏」(與遊戲內 is_collected 收藏區隔)
  - 卡片/詳情「＋收藏」加入入口
  - 繁殖表單群組整合 (#breedGroupSel + 填入 A/B + 隨機自動挑)
  - groupsAllowPartnerParent 安全預設 (false)
  - 既有 presets / setBreedPet 不被重構破壞 (回歸)
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
# §3 makeDeviceStore 重構 — 三 consumer 共用工廠 (非三份 copy)
# ----------------------------------------------------------------------------

def test_device_store_factory_present():
    """§3 重構落地:per-device localStorage 工廠。"""
    html = _t()
    assert "function makeDeviceStore" in html


def test_three_stores_use_factory():
    """三 store (autoBreed/presets/groups) 都收斂到工廠,非三份 copy。"""
    html = _t()
    assert "makeDeviceStore('autoBreed_'" in html
    assert "makeDeviceStore('flypetPresets_'" in html
    assert "makeDeviceStore('flypetGroups_'" in html


# ----------------------------------------------------------------------------
# §2 資料層 — key / load / save / CRUD / 混合成員 / STALE / partner cache
# ----------------------------------------------------------------------------

def test_groups_storage_key():
    """per-device key:'flypetGroups_' + ip()。"""
    html = _t()
    assert "flypetGroups_" in html


def test_group_load_save_present():
    """load/save API + 模組級陣列。"""
    html = _t()
    assert "function loadGroups" in html
    assert "function saveGroups" in html
    assert "var flypetGroups" in html


def test_group_crud_present():
    """建立 / 改名 / 刪除 / 刪成員 / 渲染。"""
    html = _t()
    assert "function groupNew" in html
    assert "groupRename" in html
    assert "function genGroupId" in html
    assert "data-group-rm-member" in html
    assert "function renderGroupList" in html


def test_group_add_affordance_present():
    """卡片 foot + 詳情抽屜的「＋收藏」加入入口。"""
    html = _t()
    assert "data-group-add" in html
    assert "＋收藏" in html


def test_group_panel_labeled_to_disambiguate():
    """面板文案「手選飛寵收藏」,與遊戲內 is_collected 收藏區隔。"""
    html = _t()
    assert "手選飛寵收藏" in html


def test_member_src_own_partner():
    """混合成員資料模型:own (只存 id) + partner (role_id/pet_id 可重解析)。"""
    html = _t()
    assert "src:" in html
    assert "'own'" in html
    assert "'partner'" in html
    assert "role_id" in html
    assert "pet_id" in html


def test_stale_member_handling():
    """STALE 成員偵測 + 隨機/填入挑選自動略過 (不自動刪)。"""
    html = _t()
    assert "function isStaleMember" in html
    assert ("失聯" in html) or ("STALE" in html)


def test_partner_cache_wired():
    """partner 成員 use-time 解析靠 partnerPetCache (doLoadAllPartners 寫入)。"""
    html = _t()
    assert "partnerPetCache" in html


# ----------------------------------------------------------------------------
# §1.3 partner-as-parent 安全預設旗標
# ----------------------------------------------------------------------------

def test_partner_parent_flag_safe_default():
    """groupsAllowPartnerParent 預設 false:partner 不自動入挑選池。"""
    html = _t()
    assert "groupsAllowPartnerParent" in html
    flag_block = _between(html, "groupsAllowPartnerParent", ";")
    assert "false" in flag_block


# ----------------------------------------------------------------------------
# §4.3 繁殖表單整合 — 群組選擇 + 填入 A/B + 隨機自動挑
# ----------------------------------------------------------------------------

def test_breed_form_group_integration():
    """開始交配區:群組選擇 + 填入 A/B + 隨機挑。"""
    html = _t()
    assert 'id="breedGroupSel"' in html
    assert "data-group-fill" in html
    assert "隨機" in html
    assert "data-group-pick" in html


def test_random_pick_calls_setBreedPet():
    """隨機挑接回既有 form-fill:pickRandomFromGroup 區段內呼叫 setBreedPet A/B。"""
    html = _t()
    pick_block = _between(html, "function pickRandomFromGroup", "function ")
    assert "setBreedPet('A'" in pick_block
    assert "setBreedPet('B'" in pick_block


def test_fill_from_group_present():
    """填入 A/B 走 fillBreedFromGroup,接回 setBreedPet。"""
    html = _t()
    assert "function fillBreedFromGroup" in html
    assert "function refreshBreedGroupSel" in html


# ----------------------------------------------------------------------------
# §4.4 a11y — 刪除走 lib confirmDialog (非原生 window.confirm)
# ----------------------------------------------------------------------------

def test_confirm_uses_lib_dialog():
    """群組刪除路徑用 confirmDialog (a11y),非 window.confirm。"""
    html = _t()
    del_block = _between(html, "function groupDelete", "function ")
    assert "confirmDialog(" in del_block
    assert "confirm(" not in del_block.replace("confirmDialog(", "")


# ----------------------------------------------------------------------------
# 回歸 — 既有 presets / setBreedPet 不被重構破壞
# ----------------------------------------------------------------------------

def test_existing_presets_untouched():
    """重構後 presets 行為等價:key/load/save 仍在。"""
    html = _t()
    assert "flypetPresets_" in html
    assert "function loadPresets" in html
    assert "function savePresets" in html


def test_setBreedPet_still_present():
    """群組挑選接回 setBreedPet — 它必須仍在。"""
    html = _t()
    assert "function setBreedPet" in html

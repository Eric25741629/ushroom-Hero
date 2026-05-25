"""H5SeaSession OCR-action logic for sea_v2.

Tile selection and action buttons are driven by OCR-clicking rendered labels (the
worldToScreen pixel-tap misses tiles), so the fiddly bits are: disambiguating OCR
substrings (領取 vs 已領取, 駐守 vs 駐守中, 遺跡 mis-OCR'd as 遣跡) and the
select→action→開始航行 sequencing. Those are covered here with a fake page; the live
coordinates were verified on device (2026-05-25, 5560).
"""
import pytest

from sea_v2.session import H5SeaSession, _FRAME_CENTER


def _box(text, cx, cy, w=40, h=20):
    return {"text": text, "bbox": [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]}


# ----- _ocr_matches: the substring gotchas verified live -------------------------

def test_ocr_matches_excludes_already_claimed():
    # 領取 must not also fire on 已領取 (already-claimed button)
    res = [_box("已領取", 100, 100), _box("領取", 100, 200)]
    hits = H5SeaSession._ocr_matches(res, "領取", exclude="已")
    assert [(round(x), round(y)) for x, y, _ in hits] == [(100, 200)]


def test_ocr_matches_excludes_garrisoned_status():
    # 駐守 (action button) must not fire on 駐守中 (status text)
    res = [_box("駐守中[2,26]", 270, 805), _box("駐守", 176, 420)]
    hits = H5SeaSession._ocr_matches(res, "駐守", exclude="中")
    assert [(round(x), round(y)) for x, y, _ in hits] == [(176, 420)]


def test_ocr_matches_prefers_exact_over_superstring():
    # when both exact and superstring exist, exact wins (no exclude needed)
    res = [_box("已領取", 100, 100), _box("領取", 100, 200)]
    hits = H5SeaSession._ocr_matches(res, "領取")
    assert all(t == "領取" for _, _, t in hits)


def test_ocr_matches_simplified_target_converts_to_traditional():
    import opencc
    if opencc.OpenCC("s2t").convert("资源") != "資源":
        pytest.skip("opencc stubbed by another test module; real s2t unavailable")
    res = [_box("資源Lv1", 200, 400)]
    assert H5SeaSession._ocr_matches(res, "资源")  # 简体 input still matches 繁体 render


def test_ocr_matches_empty_when_absent():
    assert H5SeaSession._ocr_matches([_box("港口", 50, 50)], "駐守") == []


# ----- fakes for the action sequencing -------------------------------------------

class _Mouse:
    def __init__(self):
        self.clicks = []

    def click(self, x, y):
        self.clicks.append((round(x), round(y)))

    def move(self, *a):
        pass

    def down(self):
        pass

    def up(self):
        pass


class FakePage:
    def __init__(self, claim_counts=None, inactive_paths=()):
        self.mouse = _Mouse()
        self._claim_counts = list(claim_counts or [])
        self._inactive = set(inactive_paths)  # node paths that report active=False

    def screenshot(self, **k):
        return b""

    def evaluate(self, js, *args):
        if args:  # _JS_NODE(path) -> node lookup; present, active unless suppressed
            path = args[0]
            return {"active": path not in self._inactive, "wx": 100, "wy": 100}
        if "n++" in js:  # _JS_CLAIMABLE_COUNT
            return self._claim_counts.pop(0) if self._claim_counts else 0
        return None  # _JS_SELECT_MENU handled by stubbed _select_menu


class StubSession(H5SeaSession):
    """Stubs the OCR + menu reads so sequencing can be asserted without a live game."""

    def __init__(self, page, ocr_boxes, menus):
        super().__init__(page, settle=0)
        self._ocr_boxes = ocr_boxes
        self._menus = list(menus)
        self.dispatched = True

    def _ocr_results(self):
        return self._ocr_boxes

    def _select_menu(self):
        return self._menus.pop(0) if self._menus else []

    def _verify_dispatched(self):
        return self.dispatched


# ----- garrison: tries candidates until one offers 駐守 --------------------------

def test_garrison_skips_owned_tile_and_garrisons_free_one():
    cx, cy = _FRAME_CENTER
    boxes = [
        _box("資源Lv1", cx - 30, cy),       # nearest centre -> tried first (owned)
        _box("資源Lv1", cx + 120, cy),      # farther -> tried second (free)
        _box("駐守", cx - 60, cy + 10),
        _box("開始航行", cx, cy + 320),
    ]
    # first selected tile: no 駐守 in menu (owned); second: 駐守 offered
    page = FakePage()
    sess = StubSession(page, boxes, menus=[[], ["駐守"]])

    from sea_v2.tiles import Tile
    assert sess.garrison(Tile("resource_1", -30954, -1630)) is True
    # both resource labels were tried, then 駐守 then 開始航行 were tapped
    assert (round(cx - 30), round(cy)) in page.mouse.clicks
    assert (round(cx + 120), round(cy)) in page.mouse.clicks
    assert (round(cx - 60), round(cy + 10)) in page.mouse.clicks   # 駐守
    assert (round(cx), round(cy + 320)) in page.mouse.clicks       # 開始航行


def test_garrison_returns_false_when_no_free_tile():
    cx, cy = _FRAME_CENTER
    boxes = [_box("資源Lv1", cx, cy)]
    page = FakePage()
    sess = StubSession(page, boxes, menus=[[], [], [], []])  # always owned
    from sea_v2.tiles import Tile
    assert sess.garrison(Tile("resource_1", -30954, -1630)) is False


# ----- attack: relic selection (跡) then 進攻 ------------------------------------

def test_attack_relic_fires_when_menu_offers_attack():
    cx, cy = _FRAME_CENTER
    boxes = [_box("遣跡", cx, cy), _box("進攻", cx - 40, cy + 10), _box("開始航行", cx, cy + 320)]
    page = FakePage()
    sess = StubSession(page, boxes, menus=[["進攻"]])
    from sea_v2.tiles import Tile
    assert sess.attack(Tile("remain", -29999, -1709)) is True
    assert (round(cx - 40), round(cy + 10)) in page.mouse.clicks   # 進攻


def test_attack_returns_false_when_attack_not_offered():
    cx, cy = _FRAME_CENTER
    boxes = [_box("遣跡", cx, cy)]
    page = FakePage()
    sess = StubSession(page, boxes, menus=[["駐守中"]])
    from sea_v2.tiles import Tile
    assert sess.attack(Tile("remain", -29999, -1709)) is False


# ----- claim: loops until cocos claimable count hits zero ------------------------

def test_claim_rewards_loops_until_none_left():
    page = FakePage(claim_counts=[3, 2, 1, 0])  # start=3 then decremented each claim
    boxes = [_box("領取", 421, 321)]
    sess = StubSession(page, boxes, menus=[])
    assert sess.claim_rewards() == 3


def test_claim_rewards_stops_when_progress_stalls():
    # claimable stays at 2 (e.g. claim scrolled off-screen) -> bail, don't loop forever
    page = FakePage(claim_counts=[2, 2, 2, 2])
    boxes = [_box("領取", 421, 321)]
    sess = StubSession(page, boxes, menus=[])
    assert sess.claim_rewards() == 0


# ----- repair: ship-repair path, gated on 大本營 ---------------------------------

def test_use_repair_kit_skips_when_ship_not_at_base():
    # ship still out (大本營 toast) through all retries -> give up cleanly (fast params)
    page = FakePage()
    boxes = [_box("僅位於大本營時才能維修船隻", 360, 600)]
    sess = StubSession(page, boxes, menus=[])
    assert sess.use_repair_kit(retries=2, wait_step=0) is False


def test_use_repair_kit_fires_when_ship_at_base():
    # no 大本營/僅位於 toast -> repair fired on first try
    page = FakePage()
    boxes = [_box("維修點恢復速率：3672/時", 360, 1083), _box("耐久度", 200, 1100)]
    sess = StubSession(page, boxes, menus=[])
    assert sess.use_repair_kit(retries=2, wait_step=0) is True


# ----- 一鍵修築 (station upgrade), gated on 木材 ----------------------------------

def test_upgrade_repair_station_fires_when_materials_ok():
    from sea_v2.session import P_ITEMGETWAY
    # ItemGetWayView (insufficient-材料 popup) NOT active -> build fired
    page = FakePage(inactive_paths={P_ITEMGETWAY})
    sess = StubSession(page, ocr_boxes=[], menus=[])
    assert sess.upgrade_repair_station() is True


def test_upgrade_repair_station_blocked_when_short_on_wood():
    # ItemGetWayView active (材料不足) -> blocked
    page = FakePage()  # all nodes active, incl ItemGetWayView
    sess = StubSession(page, ocr_boxes=[], menus=[])
    assert sess.upgrade_repair_station() is False

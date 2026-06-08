from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "fly_pet.html"


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _function_body(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start + len(start_marker))
    return text[start:end]


# ---- Bug 2: editing nest config must NOT auto-enable the nest ----
# Nest config moved from inline criteria (abCfgChange) to a preset dropdown
# (abSlotPresetChange); the regression guard follows the behavior.

def test_ab_slot_preset_change_does_not_force_enable():
    """選方案不應強制啟用巢穴；只有 abToggle 能翻 enabled。"""
    html = _template_text()
    body = _function_body(html, "function abSlotPresetChange", "function abSetStatus")

    # forced-enable removed
    assert "enabled = true;" not in body
    # forced checkbox-check removed
    assert "input[type=checkbox]" not in body
    assert ".checked = true;" not in body


def test_ab_toggle_still_owns_enabled_flag():
    """abToggle 仍是唯一翻 enabled 的地方。"""
    html = _template_text()
    body = _function_body(html, "function abToggle", "function abSlotPresetChange")
    assert "abConfig[homeId].enabled = enabled;" in body


# ---- Bug 3: per-tick throwaway copy of egg list (no shared mutation) ----

def test_auto_breed_tick_copies_egg_list():
    """autoBreedTick 須建立每輪丟棄式蛋列表副本，避免 .shift() 汙染 breedData。"""
    html = _template_text()
    body = _function_body(html, "async function autoBreedTick", "async function processAbSlot")

    # builds a copy
    assert "(breedData.egg_list || []).slice()" in body
    # does NOT pass the raw array straight into the shift path
    assert "var eggs = breedData.egg_list || [];" not in body


def test_process_ab_slot_still_shifts_the_copy():
    """processAbSlot 仍以 eggs.shift() 取蛋(現在 shift 的是副本)。"""
    html = _template_text()
    body = _function_body(html, "async function processAbSlot", "if (home.state === 1)")
    assert "var egg = eggs.shift();" in body


# ---- Bug 5: backend now blocks; remove the fixed 600ms guess ----

def test_load_breed_has_no_fixed_delay():
    """loadBreed 不再靠固定 600ms 等待,改信賴後端阻塞式刷新。"""
    html = _template_text()
    body = _function_body(html, "async function loadBreed", "async function doHatch")
    assert "setTimeout(r, 600)" not in body
    assert "600" not in body


def test_auto_breed_tick_has_no_fixed_delay():
    """autoBreedTick 不再靠固定 600ms 等待。"""
    html = _template_text()
    body = _function_body(html, "async function autoBreedTick", "async function processAbSlot")
    assert "setTimeout(r, 600)" not in body
    # the only 600 in this region was the delay; no breed-delay 600 should remain
    assert "600" not in body


def test_load_breed_still_refreshes_before_reading_info():
    """移除 delay 後仍須先刷新再讀 info,且 refresh 仍被 await。"""
    html = _template_text()
    body = _function_body(html, "async function loadBreed", "async function doHatch")
    refresh_pos = body.index("/api/fly_pet_refresh_breed/")
    info_pos = body.index("/api/fly_pet_breed_info/")
    assert refresh_pos < info_pos
    assert "await fetch('/api/fly_pet_refresh_breed/" in body


def test_auto_breed_tick_still_awaits_refresh():
    """autoBreedTick 移除 delay 後,refresh fetch 仍須被 await(保留 try/catch)。"""
    html = _template_text()
    body = _function_body(html, "async function autoBreedTick", "async function processAbSlot")
    assert "await fetch('/api/fly_pet_refresh_breed/" in body


# ---- Bug 8: consistent bot-running guard via shared helper ----

GUARD_NAME = "botRunningGuard"


def test_shared_bot_running_guard_helper_exists():
    """存在共用的 bot-running guard helper,支援 block-vs-warn。"""
    html = _template_text()
    assert "async function " + GUARD_NAME in html


def test_do_sell_blocks_via_guard():
    """doSell 為破壞性操作,須透過 guard 並在腳本運行時 BLOCK。"""
    html = _template_text()
    body = _function_body(html, "async function doSell", "function doSellOne")
    assert GUARD_NAME in body


def test_do_sell_excludes_locked_collected_and_deployed():
    """批次分解必須在前端排除鎖定、已收藏、已上陣。"""
    html = _template_text()
    body = _function_body(html, "async function doSell", "function doSellOne")

    assert "p.lock === 1" in body
    assert "p.is_collected" in body
    assert "p.fight === 1" in body
    assert "protectedIds" in body
    assert "apiPost('/api/fly_pet_resolve/'" in body
    assert "{ids: safeIds}" in body


def test_do_sell_one_blocks_via_guard():
    """doSellOne 為破壞性操作,須透過 guard 並在腳本運行時 BLOCK。"""
    html = _template_text()
    body = _function_body(html, "function doSellOne", "// ---- Breed pet selection")
    assert GUARD_NAME in body


def test_do_sell_one_rejects_collected_and_deployed():
    """單隻分解也不可送已收藏或已上陣飛寵。"""
    html = _template_text()
    body = _function_body(html, "function doSellOne", "// ---- Breed pet selection")

    assert "pet.is_collected" in body
    assert "pet.fight === 1" in body
    assert "已收藏" in body
    assert "已上陣" in body


def test_do_load_warns_via_guard_but_proceeds():
    """doLoad 為唯讀操作,須透過 guard 但只警示、不 block(仍保留斷線早退邏輯)。"""
    html = _template_text()
    body = _function_body(html, "async function doLoad", "// ---- Sell ----")
    assert GUARD_NAME in body
    # doLoad must keep its disconnect early-return logic
    assert "launchBtn.style.display = 'inline-block';" in body

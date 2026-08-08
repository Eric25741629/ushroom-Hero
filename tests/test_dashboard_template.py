"""dashboard.html step countdown must count down from an absolute deadline.

Regression (2026-06-05): the old logic relabelled "執行中 (2400s)" as "等待中"
and derived remaining seconds from the step-string digits minus `last_update`.
Because background heartbeats refresh `last_update`, the number froze (~2400) and
rendered as an ugly float (2399.8699922561646s). The fix counts down from
`info.step_deadline` (a fixed epoch) using the client clock, rounded to whole
seconds.
"""
import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def _html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_step_countdown_uses_step_deadline():
    html = _html()
    assert "info.step_deadline" in html
    # remaining is rounded to an integer second (no raw float like 2399.8699s)
    assert "Math.round(info.step_deadline" in html


def test_broken_last_update_subtraction_countdown_removed():
    html = _html()
    # the old fragile patterns are gone
    assert "等待中(" not in html
    assert r"match(/\((\d+)s\)/)" not in html
    assert "Math.floor(Date.now() / 1000) - (info.last_update" not in html


def test_device_title_is_constrained_before_status_badge():
    """長裝置名稱必須在標題欄內省略，不能穿過右側狀態徽章。"""
    html = _html()
    assert 'class="card-header-main"' in html
    assert 'class="card-header-actions"' in html
    assert ".card-header-main" in html
    assert "min-width: 0;" in html
    assert ".device-title" in html
    assert "overflow: hidden;" in html
    assert "text-overflow: ellipsis;" in html
    assert "white-space: nowrap;" in html
    assert 'title="${info.name || ip}"' in html


def test_dashboard_has_device_enable_toggle():
    """A disabled device must surface an enable control on its card.

    New devices register disabled; the dashboard reads `info.enabled` from the
    status payload and offers a toggle wired to the per-device config endpoint.
    """
    html = _html()
    assert "info.enabled" in html
    assert "toggleDeviceEnabled" in html


def test_special_wanshen_dashboard_controls_exist():
    html = _html()

    assert "跑萬神試煉・未啟用" in html
    assert "跑萬神試煉・已啟用" in html
    assert "本週已完成，不再執行" in html
    assert "/api/special_wanshen_mode/" in html
    assert "special_wanshen_account" in html
    assert "special_wanshen_completed_this_week" in html
    assert "manual_hold_until_closed: false" in html
    assert ".btn-wanshen-off" in html
    assert ".btn-wanshen-on" in html
    assert ".btn-wanshen-done" in html


def test_special_wanshen_card_has_three_mutually_exclusive_modes():
    html = _html()

    assert "special_wanshen_mode" in html
    assert "啟用完整腳本" in html
    assert "開啟網頁掛機" in html
    assert "跑萬神試煉" in html
    assert "setSpecialWanshenMode('${ip}', 'full')" in html
    assert "setSpecialWanshenMode('${ip}', 'wanshen')" in html
    assert "/api/special_wanshen_mode/" in html


def test_special_wanshen_active_modes_keep_exception_controls():
    html = _html()

    assert "specialExceptionControls" in html
    assert "deviceControl('${ip}','pause')" in html
    assert "launchWebPage('${ip}')" in html
    assert "openLiveView('${ip}')" in html
    assert "skipSleep('${ip}')" in html


def test_special_wanshen_off_card_is_compact_and_schedule_hides_countdown():
    html = _html()

    assert ".card.special-wanshen-off .wake-block" in html
    assert ".card.special-wanshen-off .info-grid" in html
    assert ".card.special-wanshen-off .progress-container" in html
    assert ".card.special-wanshen-off .log-box" in html
    assert ".card.special-wanshen-scheduled .wake-block" in html


def test_disabled_web_device_opens_web_via_login_worker():
    """A disabled web_h5 device must still be able to open the browser for
    login/setup before it is enabled.

    The normal "開啟網頁" button posts to /api/web_launch, which only writes the
    `_web_launch_requests` mailbox; every consumer of that mailbox runs inside the
    per-device automation thread. A disabled device is skipped by the scanner and
    has no thread, so a web_launch request is never consumed. The disabled card
    therefore routes through the standalone login worker (/api/web_login), the same
    path device registration uses, which drives its own Playwright session.
    """
    html = _html()
    assert "openWebForSetup" in html
    start = html.index("function openWebForSetup")
    body = html[start:start + 700]
    assert "/api/web_login/" in body
    assert "/api/web_launch/" not in body


def test_wanshen_web_buttons_use_server_routed_web_launch():
    """萬神卡片統一打 /api/web_launch，由後端依 consumer 存活狀態原子分流。"""
    html = _html()
    assert "const webNoThread" not in html
    assert "launchWebPage('${ip}', true)" in html
    assert "launchWebPage('${ip}')" in html
    assert "result.mode === 'standalone'" in html
    assert "delete _webOpenTs[ip]" in html


def test_opengold_v2_dead_flag_toggle_removed():
    """The lamp always routes to opengold_v2 (lamp_scheduler ignores the flag), so
    the settings checkbox that toggled `use_opengold_v2` is dead UI and must be gone.
    """
    html = _html()
    assert "chkOpenGoldV2" not in html
    assert "use_opengold_v2" not in html


def test_dashboard_exposes_ws_lamp_toggle():
    """WS 開神燈已存在於 config，但不能只藏在 JSON 裡。"""
    html = _html()
    assert '<option value="ws_token">' in html
    assert 'id="chkWsOpenLamp"' in html
    assert "WS 開神燈" in html
    assert "config.ws_token_open_lamp" in html
    assert "_existingWsToken.open_lamp" in html
    assert "ws_token_open_lamp: wsOpenLamp" in html
    assert "ws_token_spend: wsSelected" in html
    assert "spend: wsSelected" in html
    assert "open_lamp: wsOpenLamp" in html
    assert "function isWsPlanSelected" in html
    # WS 開神燈 / WS 挖礦是「需 +ws 方案」的獨立 opt-in：方案只控 enable/disable，
    # 不可強制打勾（regression 2026-06-14：強制打勾讓「取消勾選 WS 挖礦」存不起來，
    # 一開設定窗就被打回勾選）。
    assert "chkWsOpenLamp.checked = wsSelected" not in html
    assert "chkWsMining.checked = wsSelected" not in html
    assert "chkWsMining.disabled = !wsSelected" in html
    assert "chkWsOpenLamp.disabled = !wsSelected" in html


def test_dashboard_exposes_ws_lamp_percent_and_min_keep_inputs():
    """WS 開神燈的「百分比」與「最低保留神燈數」兩個數值輸入必須出現在設定窗，
    且在 loadConfig / saveConfig 都有讀寫對應的巢狀欄位。"""
    html = _html()
    # 兩個輸入框存在 + 文案
    assert 'id="inpWsLampPercent"' in html
    assert 'id="inpWsLampMinKeep"' in html
    assert "開神燈百分比" in html
    assert "最低保留神燈數" in html
    # loadConfig 從巢狀 ws_token 載入既有值
    assert "_existingWsToken.lamp_percent" in html
    assert "_existingWsToken.lamp_min_keep" in html
    # saveConfig 把值寫進 ws_token merge（不洗掉其他欄位）
    assert "lamp_percent: lampPercent" in html
    assert "lamp_min_keep: lampMinKeep" in html


def test_dashboard_exposes_ws_offline_fallback_toggle():
    """手機離線純 WS 掛機備援 per-device 開關必須出現在設定窗，並只在 adb+ws 方案生效。"""
    html = _html()
    # checkbox 存在 + 文案
    assert 'id="chkWsOfflineFallback"' in html
    assert "離線備援" in html
    # openSettings 載入既有值
    assert "_existingWsToken.offline_fallback" in html
    # saveConfig 把值寫進 ws_token merge（不洗掉其他欄位）
    assert "offline_fallback:" in html
    # 只在 adb+ws 方案生效（不是泛 wsSelected）
    assert "adb+ws" in html


def test_dungeon_split_into_four_independent_toggles():
    """單一 chkDungeon 拆成 4 個獨立後端開關（地獄之門/萬神試煉/雲端戰鬥/雙週賞金）。

    重構 (2026-07-02)：外層「任務開關」放每個主要任務總開關；副本從一個
    enable_dungeon 勾選拆成 4 個 enable_* granular flag，各自 load/save，id 唯一。
    舊的 chkDungeon / enable_dungeon 必須完全消失。
    """
    html = _html()
    # 舊單一開關徹底消失
    assert "chkDungeon" not in html
    assert "enable_dungeon" not in html
    # 4 個新 checkbox 各出現一次
    for cid in ("chkHellgate", "chkWanshen", "chkCloudBattle", "chkBiweekly"):
        assert f'id="{cid}"' in html, f"missing dungeon toggle id={cid}"
        assert html.count(f'id="{cid}"') == 1, f"duplicate dungeon toggle id={cid}"
    # load：各自 config.enable_X !== false
    for key in ("enable_hellgate", "enable_wanshen", "enable_cloud_battle", "enable_biweekly"):
        assert f"config.{key} !== false" in html, f"missing load for {key}"
    # save：各自寫回 payload
    assert "enable_hellgate: document.getElementById('chkHellgate').checked" in html
    assert "enable_wanshen: document.getElementById('chkWanshen').checked" in html
    assert "enable_cloud_battle: document.getElementById('chkCloudBattle').checked" in html
    assert "enable_biweekly: document.getElementById('chkBiweekly').checked" in html


def test_outer_task_toggles_host_moved_master_switches():
    """外層「任務開關」收納 8 類主要任務總開關（神燈/看廣告/跨服停車/航海從浮窗上移）。"""
    html = _html()
    for cid in ("chkFarm", "chkArena", "chkMining", "chkWsOpenLamp",
                "chkAdEnabled", "chkCarparkEnabled", "chkWsSea"):
        assert f'id="{cid}"' in html, f"missing master toggle id={cid}"
        assert html.count(f'id="{cid}"') == 1, f"duplicate master toggle id={cid}"


def test_farm_buy_seed_fertilizer_independent_checkboxes():
    """農場買種子(407)/肥料(408)改成獨立勾選：勾了才顯示數量欄，collect 依勾選寫入。"""
    html = _html()
    for cid in ("chkFarmBuy407", "chkFarmBuy408"):
        assert f'id="{cid}"' in html, f"missing farm-buy checkbox id={cid}"
        assert html.count(f'id="{cid}"') == 1, f"duplicate farm-buy checkbox id={cid}"
    # collect 依勾選 gate
    assert "document.getElementById('chkFarmBuy407').checked" in html
    assert "document.getElementById('chkFarmBuy408').checked" in html
    # 顯示切換函式
    assert "function onFarmBuyToggle" in html


def test_farm_mining_master_mirror_hidden_slaves():
    """農場/挖礦外層總開關存檔前鏡射到隱藏 slave chkWsFarm/chkWsMining。"""
    html = _html()
    assert "_sFarm.checked = document.getElementById('chkFarm').checked" in html
    assert "_sMining.checked = document.getElementById('chkMining').checked" in html


def test_device_settings_core_toggle_ids_intact():
    """收斂分組（WS 任務 / 進階摺疊區）後，所有原本的開關 input id 必須仍存在，
    否則 saveConfig/loadConfig 會抓不到元素 → 存檔壞掉。"""
    html = _html()
    for cid in (
        "chkEnabled", "chkFarm", "chkArena", "chkMining",
        "chkHellgate", "chkWanshen", "chkCloudBattle", "chkBiweekly",
        "chkRealPhone", "chkWsOpenLamp", "chkWsMining", "chkWsKungfuGuess",
        "chkWsOfflineFallback", "inpWsLampPercent", "inpWsLampMinKeep",
    ):
        assert f'id="{cid}"' in html, f"missing settings input id={cid}"
        # 每個 id 只出現一次（避免重複導致 getElementById 抓錯）
        assert html.count(f'id="{cid}"') == 1, f"duplicate settings input id={cid}"


def test_settings_ws_and_advanced_groups_are_collapsible():
    """雜亂子開關收進可摺疊群組（<details>），常用任務開關留在外層。

    WS 任務細項改成「進階設定」摺疊段內的任務類別橫向頁籤（taskTabBar）。
    """
    html = _html()
    assert "進階設定 — 各任務細項" in html
    assert 'id="taskTabBar"' in html  # 任務類別橫向頁籤條
    assert "<summary>進階設定</summary>" in html
    # 外層任務總開關群組標題（原「常用任務開關」重構為「任務開關」）
    assert ">任務開關</label>" in html


def test_device_grid_does_not_stretch_cards():
    """裝置卡片高度貼合內容，不被同列最高卡拉伸（修卡片底部留白）。"""
    html = _html()
    match = re.search(r"\.grid-container\s*\{(.*?)\}", html, re.S)
    assert match, ".grid-container rule not found"
    rule = match.group(1)
    assert "align-items: start" in rule


def test_skip_sleep_button_replaces_manual_wake_adjustment():
    html = _html()
    assert "調整喚醒" not in html
    assert "adjustWakeDelay" not in html
    assert "/api/wake_delay/" not in html


def _fly_pet_nav_tag(html: str) -> str:
    """Return the opening <button ...> tag of the 飛寵管理 side-rail entry."""
    match = re.search(r'<button[^>]*id="navFlypet"[^>]*>', html)
    assert match, "fly-pet nav entry not found in dashboard"
    return match.group(0)


def test_fly_pet_nav_item_consistent_with_buttons():
    """飛寵管理 must render like the other .nav-btn items.

    The fly-pet entry is now an in-page nav button (switchPage('flypet') → iframe),
    a bare <button class="nav-btn"> like every other side-rail item — no divergent
    inline styling. (Earlier it was an <a href="/fly-pet"> that clipped/mis-coloured.)
    """
    tag = _fly_pet_nav_tag(_html())
    assert 'class="nav-btn"' in tag
    # no divergent inline overrides that made it look different from buttons
    assert "color:inherit" not in tag
    assert "display:flex" not in tag
    assert "align-items" not in tag


def test_nav_btn_class_pins_deterministic_box_model():
    """.nav-btn must pin border-box so width:100%+padding never overflows/clips.

    Without it, only the <button> elements get the form-control border-box
    default; the <a> fly-pet entry stayed content-box and overflowed the rail.
    """
    match = re.search(r"\.nav-btn\s*\{(.*?)\}", _html(), re.S)
    assert match, ".nav-btn rule not found"
    rule = match.group(1)
    assert "box-sizing: border-box" in rule
    # the class also kills the anchor underline so no inline override is needed
    assert "text-decoration: none" in rule


def test_interrupt_buttons_route_through_locked_control():
    """暫停 / 恢復 / 強制休眠 must go through the debounced deviceControl entry,
    not the bare callApi/forceSleep that allowed rapid double-clicks.

    Regression: pressing these repeatedly fired duplicate /api requests because
    the buttons had no lock. They now carry data-ctrlbtn + a lock marker that
    survives the per-poll re-render (same pattern as the open/close-web btns).
    """
    html = _html()
    # the three interrupt buttons dispatch via deviceControl with an action
    assert "deviceControl('${ip}','pause')" in html
    assert "deviceControl('${ip}','resume')" in html
    assert "deviceControl('${ip}','force_sleep')" in html
    # the old un-debounced entrypoints are no longer wired to these buttons
    assert "onclick=\"callApi('/api/pause/${ip}')\"" not in html
    assert "onclick=\"forceSleep('${ip}')\"" not in html
    # lock marker is emitted into the button so it persists across re-render
    assert "data-ctrlbtn=" in html
    assert "ctrlBtnLocked(ip)" in html


def test_dashboard_exposes_previously_config_only_ws_controls():
    """Gap-close (2026-07-02): sea_season / recurring relic_upgrade / gacha
    free_daily + selectable mode were config-file-only; each must now have a
    dashboard control wired through the task-tabs load/collect path.
    """
    html = _html()
    # 航海 sea_season tab + bespoke fields (presence=enable, grid arrays)
    assert "['sea', '航海']" in html
    assert 'id="chkWsSea"' in html
    assert 'id="inpSeaHomeGrid"' in html
    assert 'id="inpSeaGarrisonGrid"' in html
    assert "_loadSeaTab(config)" in html
    assert "_collectSeaTab(payload)" in html
    # recurring relic auto-upgrade (distinct from relic_sprint)
    assert "ws_token.relic_upgrade" in html
    assert "ws_token.relic_max_steps" in html
    assert "ws_token.relic_fragment_floor" in html
    # gacha daily free draw + user-selectable mode (pin removed)
    assert "ws_token.gacha.free_daily" in html
    assert "ws_token.gacha.mode" in html
    assert "setPath(payload, 'ws_token.gacha.mode', 'fixed')" not in html


def test_control_button_lock_and_animation_helpers_present():
    """The press feedback (spinner+pulse animation) and the debounce lock that
    blocks repeated presses must both exist."""
    html = _html()
    # debounce lock helpers
    assert "function ctrlBtnLocked" in html
    assert "function lockCtrlBtn" in html
    assert "async function deviceControl" in html
    assert "if (!ip || ctrlBtnLocked(ip)) return;" in html
    # animation: spinner + pulse keyframes and the pending class
    assert "btn-pending" in html
    assert "@keyframes btnSpin" in html
    assert "@keyframes btnPulse" in html


def test_paused_status_uses_chinese_only():
    html = _html()
    assert "? '已暫停'" in html
    assert "已暫停 (PAUSED)" not in html


def test_dashboard_exposes_final_v1_primary_and_shadow_controls():
    html = _html()
    assert '<option value="final_v1">' in html
    assert 'id="editMiningShadowPlanner"' in html
    assert "config.mining_shadow_planner_version" in html
    assert "mining_shadow_planner_version:" in html
    assert '<option value="">關閉（預設）</option>' in html


def test_dashboard_primary_allowlist_includes_final_v1():
    html = _html()
    assert "['v1','v3','v4','final_v1'].includes(plannerVer)" in html


def test_battle_speed_card_control_exists_and_is_wired():
    """web_h5 戰鬥加速倍率必須有卡片控制項，接 /api/battle_speed 並持久化。

    Regression guard: battle_speed_scale 曾只有 config、沒有 dashboard 前端控制，
    違反「任何 opt-in 功能開關必須有 dashboard 控制」。
    """
    html = _html()
    # per-device select rendered only for web_h5 cards
    assert "id=\"battle-speed-${ip}\"" in html
    assert "info.backend === 'web_h5'" in html
    assert ">1x（關閉）</option>" in html
    assert ">2x</option>" in html
    assert ">4x</option>" in html
    # wired to the dedicated endpoint + change handler
    assert "setBattleSpeed('${ip}', this.value)" in html
    assert "/api/battle_speed/${ip}" in html
    assert "async function setBattleSpeed" in html
    # value is re-synced from status on each poll (not reset mid-change)
    assert "info.battle_speed_scale" in html
    assert "function battleSpeedOption" in html
    assert "document.activeElement !== bsSel" in html


def test_battle_mode_options_explain_themselves_and_hide_h5_only_paths_for_adb():
    """戰鬥模式要能靠懸停理解，且 ADB 不應顯示只能由 H5 執行的路徑。"""
    html = _html()

    for select_id in ("selArenaBattleMode", "selWanshenBattleMode"):
        assert f'id="{select_id}"' in html
        assert 'onchange="updateBattleModeOptions()"' in html

    # 四個選項都要有可懸停的原生 tooltip；兩個需要目前 H5 頁面的模式要標記出來。
    for value in ("animation", "local_sim", "pure_ws", "remote_calc"):
        assert re.search(rf'<option value="{value}"[^>]*title=', html)
    assert len(re.findall(
        r'<option value="(?:local_sim|remote_calc)" data-requires-h5="true"',
        html,
    )) == 4
    assert "function updateBattleModeOptions()" in html
    assert "const isH5 = backend.startsWith('web_h5')" in html
    assert "option.hidden = !isH5" in html
    assert "option.disabled = !isH5" in html
    assert "h5OnlyModes.includes(select.value)" in html
    assert "updateBattleModeOptions();" in html


def test_wanshen_battle_mode_defaults_to_pure_ws():
    html = _html()
    assert "純 WS + B 計算（預設）" in html
    assert "config.wanshen_battle_mode || 'pure_ws'" in html
    assert "wanshen_battle_mode: (document.getElementById('selWanshenBattleMode') || {}).value || 'pure_ws'" in html

import json
import os
import threading
import socket
import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_FILE = str(Path(__file__).resolve().parent / "bot_config.json")
# RLock 因 update_* 會在持有鎖時呼叫 load/save，需可重入。
_config_lock = threading.RLock()

# Process-wide mtime cache for load_config(). This is the hottest config path
# (OCR resolves config 2-4x per call through here, ~68 call sites funnel in),
# and the repo lives on a NAS/SMB share — so a naive load_config() does a NAS
# read + (auto-complete) write on EVERY call. The cache turns steady-state into
# a single os.stat(): if the file's st_mtime_ns is unchanged we return a deep
# copy of the last parsed+completed config without touching the file.
# All access is guarded by _config_lock.
_config_cache = None           # last parsed+auto-completed config dict
_config_cache_mtime_ns = None  # st_mtime_ns of CONFIG_FILE when cached
_config_cache_path = None      # CONFIG_FILE the cache was built for (path can change in tests)

# 預設的設備設定模板
DEFAULT_DEVICE_CONFIG = {
    "name": "",  # 自定義別名 (例如: 主力機)
    "enabled": True,  # 自動掛機總開關 (新裝置註冊時設 False, 登入+設定完成後手動啟用)
    "backend": "adb",  # adb / web_h5 / ws_token
    "backend_display_id": "",  # display/config binding id (empty => use device key)
    # ws_token (純 WS 後端) 設定 — 預設全 off，旗標關閉時行為與舊版完全相同。
    "use_ws_runner": False,  # True => wake loop 走 ws_token.runner.run_device，不連 ADB/Playwright
    "ws_token_spend": False,  # True => run_device 額外送花費動作 (捐獻/購物/掃蕩/續約)
    "ws_token_sweep_list": [],  # 副本管家掃蕩章節 [[id, level, times, use_ad], ...]，僅 spend 時使用
    "ws_token_open_lamp": False,  # True => run_device 額外跑開神燈 (消耗神燈道具、自動賣/裝)，預設 off
    "ws_token_kungfu_guess": False,  # True => 菇菇武道會競猜商店用粉鑽把競猜幣 4 檔買到上限 (活動沒開時伺服器擋下=no-op)，預設 off
    "ws_token_mining": None,  # {"enabled": bool, "allow_bomb": bool, "allow_drill": bool, "max_steps": int}
    "web_url": "",
    "web_canvas_selector": "canvas",
    "web_profile_dir": "playwright_profile/{device_id}",
    "web_state_file": "auth_state/{device_id}.json",
    "web_channel": "chrome",
    "web_headless": False,
    "web_clear_cookies_on_start": False,
    "web_viewport_width": 540,
    "web_viewport_height": 960,
    "web_manual_viewport_width": 0,  # 手動開啟視窗寬度 (0=使用 web_viewport_width)
    "web_manual_viewport_height": 0,  # 手動開啟視窗高度 (0=使用 web_viewport_height)
    "web_stop_mode": "keep_page",  # keep_page / blank / close_browser
    "web_screenshot_method": "playwright",  # playwright / canvas_capture
    "web_screenshot_jpeg_quality": None,  # None=PNG(無損,預設); 1..100=改用 JPEG 擷取(較快較小)
    "web_reload_after_goto": False,  # True=goto 成功後再 reload；預設關閉以加快 H5 載入
    "enable_farm": True,  # 啟用農場
    "enable_arena": True,  # 啟用競技場
    "enable_mining": True,  # 啟用挖礦
    "enable_dungeon": True,  # 啟用副本(地獄/萬神)
    "enable_shop_manager": True,  # 啟用購物管家
    "enable_dungeon_manager": True,  # 啟用副本管家
    "enable_fannaoxiao": False,  # 煩惱消 (act_type 224 左右消除小遊戲)；H5 only、每日一次、預設 off
    "is_real_phone": False,  # 是否為實體機/特殊機型 (原本的 fc65396d 邏輯)
    "keep_screen_on": False,  # 是否保持螢幕開啟 (不鎖屏)
    "screenshot_debug": False,  # 是否開啟截圖除錯
    "online_check_interval_sec": 30,  # 偵測到上線後的避讓 retry 間隔 (秒)
    "lamp_check_interval": 2,  # 開神燈/點金的間隔時間 (小時)
    "lamp_duration_sec": 300,  # 每次開神燈任務執行的總秒數
    "mining_duration_min": 6,  # 挖礦任務持續時間 (分鐘)
    "mining_planner_version": "v5",  # v1 / v3 / v4 / v5 (v5 default — priors-driven, planner-eval 2026-06-12; v2 removed)
    "mining_save_samples": False,  # save low-confidence mining cell samples
    "sleep_min_hours": 1.0,  # 每輪喚醒最短間隔（小時）
    "sleep_max_hours": 1.0,  # 每輪喚醒最長間隔（小時）
    "ws_token": {  # WS-first 階段 (game_actions/ws_phase.py)；enabled=False 完全不影響舊行為
        "enabled": False,       # 喚醒後先跑純 WS 任務，成功項由 Playwright 階段跳過
        "bootstrap_token": True, # ADB 裝置缺 capture/登入失敗時主動冷啟 App 撈 token
        "offline_fallback": False, # 手機 ADB 不可達時改跑純 WS 等待迴圈（離線備援），預設 off
        "fallback_host": "",    # 限定哪台主機跑離線備援（hostname，不分大小寫）；空 = 只有 master（防 NAS 同步雙主機注入互踢）
        # 2026-06-12 使用者指示：enabled 開了就代表全要 → 子功能預設全開
        "spend": True,          # 家族捐獻/管家代購/掃蕩/續約 等花費類
        "open_lamp": True,      # WS 開神燈（一批，取代 Playwright 開神燈）
        "lamp_percent": 0,      # WS 開神燈：依當前神燈總數的百分比決定本輪目標（0 = 不依百分比，開到沒燈）
        "lamp_min_keep": 0,     # WS 開神燈：剩餘神燈硬地板（0 = 無下限）
        "farm": None,           # {"seed_id": int, "team_cfg_id": int}；填 seed_id 才 skip 農場任務
        "dungeon_sweeps": [],   # [[type, dungeon_id, num], ...]；有配才 skip 萬神試煉
        "carpark_target": None, # 跨界停車 master_id（只停不收；legacy 單停模式）
        "carpark_auto": False,  # 跨界自動掃可停 lot（legacy 單停模式）
        "carpark_plan": {       # 日/夜窗口跨界停車（限定泊銀、優先鉑銀9/10、抱團優先；窗口內持續補停）
            "enabled": False,
            "silver_levels": [9, 10],  # 優先鉑銀 lot 等級；滿了退其他泊銀 lot
            # 跨界車位只開放台灣 10:00-22:00；夜窗 cross=0（本服車位遊戲內建自動化）
            "day":   {"window": ["10:00", "22:00"], "cross": 1},
            "night": {"window": ["22:00", "10:00"], "cross": 0},
        },
        "couple_gifts": True,   # 伴侶奶茶+玫瑰送光（每批20，server 封頂）
        "forge_ring": False,    # 戒指錘鍊（消耗全部真愛之石）
        "workshop_rotate": True,  # 加工坊 12h 兩配方輪換（couple_gifts 旁）
        "mail_claim": False,    # 每日自動領取全部郵件附件（一鍵領取，每日一次；預設關，加法功能）
        "mail_gem_threshold": None,   # 神器附魔寶石 best-effort 滿門檻（僅 log，不擋領取；None=不查）
        "mail_skill_threshold": None,  # 武魂 best-effort 滿門檻（僅 log，不擋領取；None=不查）
        "relic_upgrade": False,  # 遺物 平均強化（消耗遺物碎片強化最低等級已裝備遺物）；預設關，加法功能
        "relic_max_steps": 10,   # 遺物強化每輪步數上限
        "relic_fragment_floor": 0,  # 遺物強化：剩餘遺物碎片低於此值即停（0=無下限）
        "tycoon": False,         # 傳奇大亨（大富翁）自動擲骰；免費骰子純收益，活動沒開=no-op；預設關
        "tycoon_max_rolls": 50,  # 傳奇大亨每輪擲骰次數上限
        "mining": {             # WS 挖礦；成功後可跳過 Playwright 挖礦任務
            "enabled": True,
            "allow_bomb": False,
            "allow_drill": False,
            "max_steps": 200,
        },
    },
}


@dataclass
class DeviceConfig:
    """Typed device configuration. Replaces plain dict from get_device_config().

    Known fields mirror DEFAULT_DEVICE_CONFIG. Unknown/future keys are preserved
    in _extra and are accessible via .get() for backward compatibility.
    """

    # Identity
    name: str = ""
    device_id: str = ""

    # Lifecycle — auto-start master switch (False until the user enables it;
    # missing key in legacy config reads as True via from_dict default).
    enabled: bool = True

    # Backend
    backend: str = "adb"
    backend_display_id: str = ""

    # ws_token (pure-WS backend) — additive, default off so legacy devices are
    # unaffected. When use_ws_runner is True the wake loop runs
    # ws_token.runner.run_device instead of the ADB/Playwright daily pipeline.
    use_ws_runner: bool = False
    ws_token_spend: bool = False
    ws_token_sweep_list: list = field(default_factory=list)
    ws_token_open_lamp: bool = False
    ws_token_kungfu_guess: bool = False
    ws_token_mining: Optional[dict] = None

    # Web H5 settings
    web_url: str = ""
    web_canvas_selector: str = "canvas"
    web_profile_dir: str = "playwright_profile/{device_id}"
    web_state_file: str = "auth_state/{device_id}.json"
    web_channel: str = "chrome"
    web_headless: bool = False
    web_clear_cookies_on_start: bool = False
    web_viewport_width: int = 540
    web_viewport_height: int = 960
    web_manual_viewport_width: int = 0
    web_manual_viewport_height: int = 0
    web_stop_mode: str = "keep_page"
    web_screenshot_method: str = "playwright"
    web_screenshot_jpeg_quality: Optional[int] = None
    web_reload_after_goto: bool = False

    # Feature flags
    enable_farm: bool = True
    enable_arena: bool = True
    enable_mining: bool = True
    enable_dungeon: bool = True
    enable_shop_manager: bool = True
    enable_dungeon_manager: bool = True
    enable_fannaoxiao: bool = False

    # Device behaviour
    is_real_phone: bool = False
    keep_screen_on: bool = False
    screenshot_debug: bool = False
    online_check_interval_sec: int = 30

    # Task durations / intervals
    lamp_check_interval: int = 2
    lamp_duration_sec: int = 300
    mining_duration_min: int = 6
    mining_planner_version: str = "v5"
    mining_save_samples: bool = False

    # Sleep schedule
    sleep_min_hours: float = 1.0
    sleep_max_hours: float = 1.0

    # Unknown / future keys preserved here for backward compat
    _extra: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DeviceConfig":
        """Build a DeviceConfig from a raw config dict.

        Known fields are mapped to typed attributes; unknown keys are stored in
        _extra so that callers using .get() on non-schema keys still work.
        """
        known = {
            f for f in cls.__dataclass_fields__
            if not f.startswith("_")
        }
        kwargs = {k: v for k, v in raw.items() if k in known}
        extra = {k: v for k, v in raw.items() if k not in known}
        obj = cls(**kwargs)
        obj._extra = extra
        return obj

    def get(self, key: str, default: Any = None) -> Any:
        """Backward-compatible dict-style access.

        Checks typed attributes first, then _extra, then returns default.
        Prefer direct attribute access (cfg.enable_farm) in new code.
        """
        if key in self.__dataclass_fields__ and not key.startswith("_"):
            return getattr(self, key)
        return self._extra.get(key, default)


# OCR 全域設定
DEFAULT_OCR_CONFIG = {
    "servers": [
        "http://100.64.0.5:5001",
        "http://100.64.0.7:5001",
        "http://localhost:5001",
    ],
    "server_mode": "main",  # main / backup / auto
    "timeout_sec": 20,
    "img_decode_retries": 3,
    "ocr_empty_retries": 2,
    "retry_delay_sec": 0.6,
    # 可選：全域預設 OCR 擷取區域（給前端可填寫）
    "default_region_enabled": False,
    "default_x_range": [0, 0],
    "default_y_range": [0, 0],
}

# 預設的全域設定 (包含多主機範例)
DEFAULT_GLOBAL_CONFIG = {
    # 預設值 (當找不到特定主機設定時使用)
    "mode": "master",
    "master_url": "http://127.0.0.1:5002",
    "worker_id": "unknown_worker",
    "worker_sync_timeout_sec": 10.0,
    "worker_sync_failure_backoff_sec": 6.0,
    # 跨裝置 online-check 的 checker 候選清單。任一在此清單、目前空閒、且好友
    # 列表含 target 的帳號都可代為查線。預設只有 emulator-5554，與舊行為一致
    # （5558 仍只被 5554 服務）。
    "online_check_checkers": ["emulator-5554"],
    "ocr": copy.deepcopy(DEFAULT_OCR_CONFIG),
    # 針對特定電腦名稱的設定 (解決 NAS 共用檔案問題)
    # 格式: "COMPUTER_NAME": { 設定覆蓋 }
    "host_settings": {
        "DESKTOP-OV0ASQ4": {
            "mode": "worker",
            "master_url": "https://mushroom1_dashboard.infinite25741629.uk",
            "worker_id": "desktop_ov0asq4",
        },
        "YOUR-DORM-PC-NAME": {"mode": "master"},
        "YOUR-LAPTOP-NAME": {
            "mode": "worker",
            "master_url": "http://127.0.0.1:5002",
            "worker_id": "laptop_worker",
        },
    },
}


def get_hostname() -> str:
    return socket.gethostname()


def _backup_file() -> str:
    """Host-specific last-known-good backup path.

    Per-host filename so the backup itself never produces Syncthing
    conflicts across machines sharing the synced repo.
    """
    safe_host = "".join(c if c.isalnum() or c in "-_." else "_" for c in get_hostname())
    return str(Path(CONFIG_FILE).with_name(f"bot_config.{safe_host}.bak"))


def _write_backup(data: Dict[str, Any]) -> None:
    """Persist a last-known-good snapshot. Only back up *non-empty* configs so a
    transient empty/default state can never become the recovery anchor."""
    try:
        if not data or not data.get("devices"):
            return
        with open(_backup_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:  # backup is best-effort, never fatal
        logger.warning(f"[Config] 寫入備份失敗: {e}")


def _load_backup() -> "Dict[str, Any] | None":
    """Return last-known-good config from the host backup, or None."""
    path = _backup_file()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if data and data.get("devices"):
            return data
    except Exception as e:
        logger.warning(f"[Config] 讀取備份失敗: {e}")
    return None



# -- type-coercion helpers shared by update_ocr_config / update_device_config --

def _to_int(v: Any, d: int) -> int:
    try:
        return int(v)
    except Exception:
        return d


def _to_float(v: Any, d: float) -> float:
    try:
        return float(v)
    except Exception:
        return d


def _to_bool(v: Any, d: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        text = v.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return d


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def _clamp_float(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return default


def _enum_str(v: Any, allowed: set, default: str) -> str:
    s = str(v).strip().lower()
    return s if s in allowed else default


def _sanitize_sweep_list(v: Any) -> list:
    """Coerce a ws_token_sweep_list into ``[[int, ...], ...]``.

    Each chapter entry is ``[id, level, times[, use_ad]]``; non-list / malformed
    entries are dropped. Invalid input collapses to ``[]`` so a bad value can
    never persist a non-list (which steward's sweep would choke on).
    """
    if not isinstance(v, list):
        return []
    out: list = []
    for entry in v:
        if not isinstance(entry, (list, tuple)):
            continue
        row: list = []
        ok = True
        for part in entry:
            try:
                row.append(int(part))
            except (TypeError, ValueError):
                ok = False
                break
        if ok and len(row) >= 3:
            out.append(row[:4])
    return out


def _sanitize_mining_config(v: Any) -> Optional[dict]:
    """Coerce WS mining config; invalid input disables pure-WS mining.

    max_steps 上限保守設 500，避免設定錯誤造成一輪喚醒無界消耗。
    """
    if v is None:
        return None
    if not isinstance(v, dict):
        return None
    out = {
        "enabled": _to_bool(v.get("enabled"), False),
        "allow_bomb": _to_bool(v.get("allow_bomb"), False),
        "allow_drill": _to_bool(v.get("allow_drill"), False),
        "max_steps": _clamp_int(v.get("max_steps"), 1, 500, 200),
    }
    if v.get("max_depth") is not None:
        out["max_depth"] = max(1, _to_int(v.get("max_depth"), 1))
    if v.get("timeout") is not None:
        out["timeout"] = max(0.1, _to_float(v.get("timeout"), 8.0))
    return out


def _merge_carpark_plan(v: Any, default: dict) -> dict:
    """Coerce ws_token.carpark_plan; malformed input degrades to defaults.

    enabled -> bool; each window needs ["HH:MM", "HH:MM"] (else that window
    falls back to the default); cross clamped to 0..10; silver_levels kept
    only when a list of ints in 1..30 (鉑銀1..30).
    """
    out = copy.deepcopy(default)
    if not isinstance(v, dict):
        return out
    out["enabled"] = _to_bool(v.get("enabled"), default["enabled"])
    levels = v.get("silver_levels")
    if isinstance(levels, (list, tuple)):
        clean = [int(x) for x in levels
                 if isinstance(x, (int, float)) and 1 <= int(x) <= 30]
        if clean:
            out["silver_levels"] = clean

    def _hhmm_ok(s: Any) -> bool:
        if not isinstance(s, str) or s.count(":") != 1:
            return False
        h, _, m = s.partition(":")
        return (h.isdigit() and m.isdigit()
                and 0 <= int(h) <= 23 and 0 <= int(m) <= 59)

    for name in ("day", "night"):
        w = v.get(name)
        if not isinstance(w, dict):
            continue
        win = w.get("window")
        if (isinstance(win, (list, tuple)) and len(win) == 2
                and _hhmm_ok(win[0]) and _hhmm_ok(win[1])):
            out[name]["window"] = [str(win[0]), str(win[1])]
        out[name]["cross"] = _clamp_int(w.get("cross"), 0, 10,
                                        default[name]["cross"])
    return out


def _merge_ws_token_phase_config(v: Any) -> dict:
    """Merge nested ws_token config with defaults, including mining defaults."""
    default = copy.deepcopy(DEFAULT_DEVICE_CONFIG["ws_token"])
    if not isinstance(v, dict):
        return default
    merged = copy.deepcopy(default)
    merged.update(v)
    merged["bootstrap_token"] = _to_bool(
        merged.get("bootstrap_token"),
        default["bootstrap_token"],
    )
    merged["offline_fallback"] = _to_bool(
        merged.get("offline_fallback"),
        default["offline_fallback"],
    )
    mining_cfg = _sanitize_mining_config(merged.get("mining"))
    merged["mining"] = mining_cfg or copy.deepcopy(default["mining"])
    # 開神燈百分比 / 最低保留：防呆轉型（壞值退回預設 0）。
    merged["lamp_percent"] = max(
        0.0, _to_float(merged.get("lamp_percent"), default["lamp_percent"]))
    merged["lamp_min_keep"] = max(
        0, _to_int(merged.get("lamp_min_keep"), default["lamp_min_keep"]))
    merged["carpark_auto"] = _to_bool(merged.get("carpark_auto"),
                                      default["carpark_auto"])
    merged["carpark_plan"] = _merge_carpark_plan(merged.get("carpark_plan"),
                                                 default["carpark_plan"])
    # 遺物強化 (SPENDS 遺物碎片) / 傳奇大亨擲骰：防呆轉型，壞值退回預設。
    merged["relic_upgrade"] = _to_bool(merged.get("relic_upgrade"),
                                       default["relic_upgrade"])
    merged["relic_max_steps"] = max(
        0, _to_int(merged.get("relic_max_steps"), default["relic_max_steps"]))
    merged["relic_fragment_floor"] = max(
        0, _to_int(merged.get("relic_fragment_floor"),
                   default["relic_fragment_floor"]))
    merged["tycoon"] = _to_bool(merged.get("tycoon"), default["tycoon"])
    merged["tycoon_max_rolls"] = max(
        0, _to_int(merged.get("tycoon_max_rolls"), default["tycoon_max_rolls"]))
    return merged


def _invalidate_config_cache() -> None:
    """Force the next load_config() to re-read from disk.

    Called by every in-process writer of bot_config.json so that a fresh
    save is reflected immediately instead of being masked by a stale cache.
    """
    global _config_cache_mtime_ns
    _config_cache_mtime_ns = None


def load_config() -> Dict[str, Any]:
    """讀取完整設定檔，並自動補全缺失的欄位。

    Process-wide mtime cache: on a cache hit (file unchanged since last load)
    returns a deep copy of the cached config without any file I/O. The
    auto-complete rewrite and last-known-good backup only run on a cache miss.
    """
    global _config_cache, _config_cache_mtime_ns, _config_cache_path
    with _config_lock:
        # --- Fast path: mtime cache hit ---
        # Always re-resolve CONFIG_FILE (tests reassign it) and compare both the
        # path and st_mtime_ns so a cache built for a different file can't leak.
        try:
            st = os.stat(CONFIG_FILE)
            current_mtime_ns = st.st_mtime_ns
        except OSError:
            current_mtime_ns = None

        if (
            _config_cache is not None
            and current_mtime_ns is not None
            and _config_cache_mtime_ns == current_mtime_ns
            and _config_cache_path == CONFIG_FILE
        ):
            # MUST deepcopy: callers do .update()/.copy() and mutate the result.
            return copy.deepcopy(_config_cache)

        if not os.path.exists(CONFIG_FILE):
            # 初始化預設設定
            default = {"devices": {}, "global": copy.deepcopy(DEFAULT_GLOBAL_CONFIG)}
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(default, f, ensure_ascii=False, indent=4)
                # Cache the freshly written default so the next call is a hit.
                _config_cache = copy.deepcopy(default)
                _config_cache_mtime_ns = os.stat(CONFIG_FILE).st_mtime_ns
                _config_cache_path = CONFIG_FILE
            except Exception as e:
                logger.error(f"[Config] Failed to create default config: {e}")
                _invalidate_config_cache()
            return copy.deepcopy(default)

        try:
            # Accept UTF-8 with or without BOM to avoid parser failures after external edits.
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)

            # --- 自動補全邏輯 ---
            changed = False

            # 1. 補全 global
            if "global" not in data:
                data["global"] = DEFAULT_GLOBAL_CONFIG.copy()
                changed = True
            else:
                # 補全 global 內部的缺失鍵值 (例如 master_url, worker_id)
                for k, v in DEFAULT_GLOBAL_CONFIG.items():
                    if k not in data["global"]:
                        data["global"][k] = copy.deepcopy(v)
                        changed = True

                # 2. 補全 host_settings
                if "host_settings" not in data["global"]:
                    data["global"]["host_settings"] = DEFAULT_GLOBAL_CONFIG[
                        "host_settings"
                    ].copy()
                    changed = True
                else:
                    # 補全缺少的主機設定，避免共用設定檔被舊內容覆蓋後失效
                    for host_name, host_cfg in DEFAULT_GLOBAL_CONFIG[
                        "host_settings"
                    ].items():
                        if host_name not in data["global"]["host_settings"]:
                            data["global"]["host_settings"][host_name] = host_cfg.copy()
                            changed = True

                # 3. 補全 OCR 設定
                if "ocr" not in data["global"] or not isinstance(
                    data["global"].get("ocr"), dict
                ):
                    data["global"]["ocr"] = copy.deepcopy(DEFAULT_OCR_CONFIG)
                    changed = True
                else:
                    ocr_cfg = data["global"]["ocr"]
                    for ok, ov in DEFAULT_OCR_CONFIG.items():
                        if ok not in ocr_cfg:
                            ocr_cfg[ok] = copy.deepcopy(ov)
                            changed = True

            # 如果有修補，寫回檔案
            if changed:
                try:
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                    logger.info("[Config] 已自動補全缺失的設定欄位")
                    # The rewrite bumped the file mtime. Re-stat AFTER the write so
                    # the cached mtime matches disk — otherwise the next call would
                    # see a mismatch and re-read + re-auto-complete forever.
                    current_mtime_ns = os.stat(CONFIG_FILE).st_mtime_ns
                except Exception as e:
                    logger.error(f"[Config] 寫回設定失敗: {e}")
                    # Rewrite failed: don't trust current_mtime_ns; skip caching by
                    # leaving it None so the next call re-reads.
                    current_mtime_ns = None

            # 成功讀到有效（非空）設定才更新 last-known-good 備份。
            _write_backup(data)

            # Populate the cache only on this happy path. We cache a deep copy so
            # mutations to the returned dict can't corrupt the cached state.
            if current_mtime_ns is not None:
                _config_cache = copy.deepcopy(data)
                _config_cache_mtime_ns = current_mtime_ns
                _config_cache_path = CONFIG_FILE
            else:
                _invalidate_config_cache()
            return copy.deepcopy(data)
        except Exception as e:
            logger.error(f"[Config] 讀取失敗: {e}")
            # 自癒：解析失敗（例如同步中途/衝突檔）時，回復上次成功的設定，
            # 絕不回傳空預設——否則後續 save 會把整份設定清空。
            # Do NOT populate the cache on this self-heal path.
            _invalidate_config_cache()
            recovered = _load_backup()
            if recovered is not None:
                logger.warning("[Config] 已從 host 備份自癒回復設定，未使用空預設")
                return recovered
            return {"devices": {}, "global": copy.deepcopy(DEFAULT_GLOBAL_CONFIG)}


def get_global_config() -> Dict[str, Any]:
    """
    獲取當前電腦的全域設定。
    優先順序: host_settings[hostname] > global 預設值
    """
    config = load_config()
    global_cfg = config.get("global", copy.deepcopy(DEFAULT_GLOBAL_CONFIG))

    hostname = get_hostname().strip()
    host_settings = global_cfg.get("host_settings", {})

    # 取出預設值 (移除 host_settings 避免混淆)
    final_cfg = {k: v for k, v in global_cfg.items() if k != "host_settings"}

    # 如果有針對這台電腦的設定，進行覆蓋
    matched_key = None
    if hostname in host_settings:
        matched_key = hostname
    else:
        hostname_upper = hostname.upper()
        for key in host_settings.keys():
            if key.strip().upper() == hostname_upper:
                matched_key = key
                break

    if matched_key is not None:
        final_cfg.update(host_settings[matched_key])

    return final_cfg


def get_online_check_checkers() -> "list[str]":
    """Return the list of device serials allowed to serve cross-device
    online-check requests.

    Source of truth: global config `online_check_checkers`. Defaults to
    `["emulator-5554"]` (legacy behaviour) when missing or malformed. Entries
    are trimmed and de-duplicated while preserving order.
    """
    try:
        raw = get_global_config().get("online_check_checkers")
    except Exception:
        raw = None
    if not isinstance(raw, list) or not raw:
        return list(DEFAULT_GLOBAL_CONFIG["online_check_checkers"])
    seen: set = set()
    checkers: "list[str]" = []
    for item in raw:
        ip = str(item).strip()
        if ip and ip not in seen:
            seen.add(ip)
            checkers.append(ip)
    if not checkers:
        return list(DEFAULT_GLOBAL_CONFIG["online_check_checkers"])
    return checkers


def get_ocr_config() -> Dict[str, Any]:
    """取得 OCR 全域設定（含預設補齊）。"""
    global_cfg = get_global_config()
    raw = global_cfg.get("ocr", {}) if isinstance(global_cfg, dict) else {}

    merged = copy.deepcopy(DEFAULT_OCR_CONFIG)
    if isinstance(raw, dict):
        merged.update(raw)

    # 基本防呆
    if not isinstance(merged.get("servers"), list):
        merged["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    merged["servers"] = [
        str(s).strip().rstrip("/") for s in merged["servers"] if str(s).strip()
    ]
    mode = str(merged.get("server_mode", "main")).strip().lower()
    merged["server_mode"] = mode if mode in {"main", "backup", "auto"} else "main"
    if not merged["servers"]:
        merged["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

    return merged


def update_ocr_config(new_settings: Dict[str, Any]):
    """更新 OCR 全域設定（寫入 bot_config.json 的 global.ocr）。"""
    # 整段 load + modify + save 必須在同一個 critical section，
    # 否則其他 thread 可能在中途以舊資料 save_config 蓋掉本次變更。
    with _config_lock:
        config = load_config()
        if "global" not in config:
            config["global"] = copy.deepcopy(DEFAULT_GLOBAL_CONFIG)

        current = config["global"].get("ocr", copy.deepcopy(DEFAULT_OCR_CONFIG))
        if not isinstance(current, dict):
            current = copy.deepcopy(DEFAULT_OCR_CONFIG)

        current.update(new_settings or {})

        servers = current.get("servers", [])
        if isinstance(servers, str):
            servers = [s.strip() for s in servers.splitlines() if s.strip()]
        if not isinstance(servers, list):
            servers = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])
        current["servers"] = [str(s).strip().rstrip("/") for s in servers if str(s).strip()]
        if not current["servers"]:
            current["servers"] = copy.deepcopy(DEFAULT_OCR_CONFIG["servers"])

        current["server_mode"] = _enum_str(
            current.get("server_mode", "main"), {"main", "backup", "auto"}, "main"
        )
        current["timeout_sec"] = max(
            1, _to_int(current.get("timeout_sec"), DEFAULT_OCR_CONFIG["timeout_sec"])
        )
        current["img_decode_retries"] = max(
            1, _to_int(current.get("img_decode_retries"), DEFAULT_OCR_CONFIG["img_decode_retries"])
        )
        current["ocr_empty_retries"] = max(
            0, _to_int(current.get("ocr_empty_retries"), DEFAULT_OCR_CONFIG["ocr_empty_retries"])
        )
        current["retry_delay_sec"] = max(
            0.0, _to_float(current.get("retry_delay_sec"), DEFAULT_OCR_CONFIG["retry_delay_sec"])
        )
        current["default_region_enabled"] = bool(current.get("default_region_enabled", False))
        for key in ["default_x_range", "default_y_range"]:
            val = current.get(key, [0, 0])
            if not isinstance(val, list) or len(val) != 2:
                val = [0, 0]
            current[key] = [_to_int(val[0], 0), _to_int(val[1], 0)]

        config["global"]["ocr"] = current
        save_config(config)
        logger.info("[Config] 已更新 OCR 全域設定")


def save_config(config: Dict[str, Any], *, allow_empty_devices: bool = False):
    """寫入設定檔。

    Safety guard: 若傳入的 config 的 devices 為空，但磁碟上既有檔案有裝置，
    預設拒絕寫入（避免一次解析失敗/同步衝突就清空整份設定）。
    確需清空時請明確傳入 ``allow_empty_devices=True``。
    """
    with _config_lock:
        # Any disk write here makes the mtime cache stale. Invalidate so the next
        # load_config() re-reads. (update_ocr_config / update_device_config both
        # route their writes through here, so this one site covers all writers.)
        _invalidate_config_cache()
        try:
            incoming_devices = (config or {}).get("devices") or {}
            if not incoming_devices and not allow_empty_devices:
                existing_has_devices = False
                if os.path.exists(CONFIG_FILE):
                    try:
                        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                            existing_has_devices = bool(json.load(f).get("devices"))
                    except Exception:
                        existing_has_devices = False
                if existing_has_devices:
                    logger.error(
                        "[Config] 已拒絕以空 devices 覆蓋既有非空設定 (safety guard)。"
                        "如確需清空請呼叫 save_config(config, allow_empty_devices=True)"
                    )
                    return
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            _write_backup(config)
        except Exception as e:
            logger.error(f"[Config] 寫入失敗: {e}")


def _get_raw_device_config(ip: str) -> Dict[str, Any]:
    """Internal: return the raw merged device config dict (used by update_device_config)."""
    config = load_config()
    devices = config.get("devices", {})

    if ip not in devices:
        default = copy.deepcopy(DEFAULT_DEVICE_CONFIG)
        default["name"] = ip  # 預設名稱就是 IP
        return default

    # Ensure old config files also have new fields (Migration)
    user_config = devices[ip]
    merged_config = copy.deepcopy(DEFAULT_DEVICE_CONFIG)
    merged_config.update(user_config)
    merged_config["ws_token"] = _merge_ws_token_phase_config(user_config.get("ws_token"))

    return merged_config


def get_device_config(ip: str) -> "DeviceConfig":
    """獲取單一設備的設定，如果不存在則返回預設值。

    Returns a typed DeviceConfig. Existing callers using .get() continue to work
    via the DeviceConfig.get() shim.
    """
    return DeviceConfig.from_dict(_get_raw_device_config(ip))


def get_device_config_dict(ip: str) -> Dict[str, Any]:
    """Return device config as a raw dict (including all keys, even those not
    in the typed DeviceConfig schema such as `carpark`, `statue_weekly`,
    `experimental_cocos_navigation`).

    Use this when the caller needs to serialize the full config (e.g. Flask
    `jsonify`) — `DeviceConfig` puts unknown keys in `_extra`, which jsonify
    leaks as a nested `_extra` field instead of flattening them, hiding fields
    from frontend code that reads `config.<key>` directly.
    """
    return _get_raw_device_config(ip)


def update_device_config(ip: str, new_settings: Dict[str, Any]):
    """Update per-device config with validation/sanitization."""
    # 整段 load + modify + save 必須在同一個 critical section，
    # 否則其他 thread 可能在中途以舊資料 save_config 蓋掉本次變更。
    with _config_lock:
        config = load_config()
        if "devices" not in config:
            config["devices"] = {}

        current = config["devices"].get(ip, copy.deepcopy(DEFAULT_DEVICE_CONFIG))
        current.update(new_settings or {})

        current["backend"] = _enum_str(current.get("backend", "adb"), {"adb", "web_h5", "ws_token"}, "adb")
        current["backend_display_id"] = str(current.get("backend_display_id", "")).strip()
        current["use_ws_runner"] = (
            True
            if current["backend"] == "ws_token"
            else _to_bool(
                current.get("use_ws_runner", DEFAULT_DEVICE_CONFIG["use_ws_runner"]),
                DEFAULT_DEVICE_CONFIG["use_ws_runner"],
            )
        )
        current["ws_token_spend"] = _to_bool(
            current.get("ws_token_spend", DEFAULT_DEVICE_CONFIG["ws_token_spend"]),
            DEFAULT_DEVICE_CONFIG["ws_token_spend"],
        )
        current["ws_token_sweep_list"] = _sanitize_sweep_list(current.get("ws_token_sweep_list"))
        current["ws_token_open_lamp"] = _to_bool(
            current.get("ws_token_open_lamp", DEFAULT_DEVICE_CONFIG["ws_token_open_lamp"]),
            DEFAULT_DEVICE_CONFIG["ws_token_open_lamp"],
        )
        current["ws_token_kungfu_guess"] = _to_bool(
            current.get("ws_token_kungfu_guess",
                        DEFAULT_DEVICE_CONFIG["ws_token_kungfu_guess"]),
            DEFAULT_DEVICE_CONFIG["ws_token_kungfu_guess"],
        )
        current["ws_token_mining"] = _sanitize_mining_config(current.get("ws_token_mining"))
        current["ws_token"] = _merge_ws_token_phase_config(current.get("ws_token"))
        current["web_url"] = str(current.get("web_url", "")).strip()
        current["web_canvas_selector"] = (
            str(current.get("web_canvas_selector", "canvas")).strip() or "canvas"
        )
        current["web_profile_dir"] = (
            str(current.get("web_profile_dir", "playwright_profile/{device_id}")).strip()
            or "playwright_profile/{device_id}"
        )
        current["web_state_file"] = (
            str(current.get("web_state_file", "auth_state/{device_id}.json")).strip()
            or "auth_state/{device_id}.json"
        )
        current["web_channel"] = str(current.get("web_channel", "chrome")).strip() or "chrome"
        current["web_headless"] = _to_bool(current.get("web_headless", False), False)
        current["web_clear_cookies_on_start"] = _to_bool(
            current.get("web_clear_cookies_on_start", False), False
        )
        current["web_reload_after_goto"] = _to_bool(
            current.get("web_reload_after_goto", False), False
        )
        current["web_viewport_width"] = _clamp_int(
            current.get("web_viewport_width"), 200, 4096, DEFAULT_DEVICE_CONFIG["web_viewport_width"]
        )
        current["web_viewport_height"] = _clamp_int(
            current.get("web_viewport_height"), 200, 4096, DEFAULT_DEVICE_CONFIG["web_viewport_height"]
        )
        current["web_manual_viewport_width"] = _clamp_int(
            current.get("web_manual_viewport_width"), 0, 4096, DEFAULT_DEVICE_CONFIG["web_manual_viewport_width"]
        )
        current["web_manual_viewport_height"] = _clamp_int(
            current.get("web_manual_viewport_height"), 0, 4096, DEFAULT_DEVICE_CONFIG["web_manual_viewport_height"]
        )
        # keep_page / blank / close / close_page / close_browser
        current["web_stop_mode"] = _enum_str(
            current.get("web_stop_mode", "keep_page"),
            {"keep_page", "blank", "close", "close_page", "close_browser"},
            "keep_page",
        )
        current["web_screenshot_method"] = _enum_str(
            current.get("web_screenshot_method", "playwright"),
            {"playwright", "canvas_capture"},
            "playwright",
        )
        current["online_check_interval_sec"] = _clamp_int(
            current.get("online_check_interval_sec"), 5, 3600,
            DEFAULT_DEVICE_CONFIG["online_check_interval_sec"]
        )
        current["lamp_check_interval"] = _clamp_int(
            current.get("lamp_check_interval"), 1, 24, DEFAULT_DEVICE_CONFIG["lamp_check_interval"]
        )
        current["lamp_duration_sec"] = _clamp_int(
            current.get("lamp_duration_sec"), 30, 3600, DEFAULT_DEVICE_CONFIG["lamp_duration_sec"]
        )
        current["mining_duration_min"] = _clamp_int(
            current.get("mining_duration_min"), 1, 60, DEFAULT_DEVICE_CONFIG["mining_duration_min"]
        )
        current["mining_planner_version"] = _enum_str(
            current.get("mining_planner_version", DEFAULT_DEVICE_CONFIG["mining_planner_version"]),
            {"v1", "v3", "v4", "v5"},
            "v1",
        )
        current["mining_save_samples"] = _to_bool(
            current.get("mining_save_samples", DEFAULT_DEVICE_CONFIG["mining_save_samples"]),
            DEFAULT_DEVICE_CONFIG["mining_save_samples"],
        )
        _sleep_min = _clamp_float(
            current.get("sleep_min_hours"), 0.25, 24.0, DEFAULT_DEVICE_CONFIG["sleep_min_hours"]
        )
        _sleep_max = _clamp_float(
            current.get("sleep_max_hours"), 0.25, 24.0, DEFAULT_DEVICE_CONFIG["sleep_max_hours"]
        )
        current["sleep_min_hours"] = _sleep_min
        current["sleep_max_hours"] = max(_sleep_min, _sleep_max)
        for k in [
            "enabled",
            "enable_farm",
            "enable_arena",
            "enable_mining",
            "enable_dungeon",
            "enable_shop_manager",
            "enable_dungeon_manager",
            "enable_fannaoxiao",
            "is_real_phone",
            "keep_screen_on",
            "screenshot_debug",
        ]:
            if k in current:
                current[k] = bool(current[k])

        current["name"] = str(current.get("name", ip)).strip() or ip

        config["devices"][ip] = current
        save_config(config)
        logger.info(f"[Config] Updated device config: {ip} ({current.get('name')})")


def get_flag(ip: str, key: str, default=False) -> bool:
    """
    [給邏輯層使用] 快速獲取某個開關的狀態
    用法: if config_manager.get_flag(ip, 'is_real_phone'): ...
    """
    cfg = get_device_config(ip)
    return cfg.get(key, default)


def is_device_enabled(ip: str) -> bool:
    """裝置是否允許被掃描自動啟動掛機 thread。

    缺 `enabled` 鍵的舊設定一律視為已啟用 (向後相容)；只有明確 `enabled: false`
    (新裝置註冊後的預設) 才會回 False，讓掃描器在使用者手動啟用前先別開這台。
    """
    return bool(_get_raw_device_config(ip).get("enabled", True))

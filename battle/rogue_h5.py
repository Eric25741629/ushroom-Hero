"""萬神試煉 H5 driver — cocos 場景狀態判斷 + node.emit('click') 操作（零 OCR、零截圖）。

只用於 web_h5 後端；ADB 仍走 weekly_trials.py 的 OCR 路徑。

為什麼這樣寫（實測根因，見 docs/superpowers/plans/2026-07-17-wanshen-h5-node-ws-plan.md）：
- 開局獎勵 `RogueGoodsGetView` 有全螢幕 `Block`(1500x3000) 吞掉「開始挑戰」的座標 tap
  → 舊 OCR 路徑首關空燒 _STAGE_RESULT_TIMEOUT(150s)。`emit('click')` 繞過 z-order/遮罩，一發即中。
- 結算「結束本局」座標 (510,920) 會打偏 → 改 emit `RogueMainView/view/btnClose`。

判斷來源：cocos 場景 node.active / 特定 label（即 WS 結果的 render，較 OCR 精確且免截圖）。
所有節點路徑 + 狀態機皆 2026-07-17 於 5556 live 實測。判斷邏輯集中在 `read_state()` 單一 seam，
未來要改讀原始 WS frame 只需換這個函式。

用法：
    from battle import rogue_h5
    completed = rogue_h5.run_rounds(page, rounds=10)   # page = playwright sync Page
    # 或由 weekly_trials 經 device 取 page：page = getattr(d, "_page", None)
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

try:
    from utils.logging_utils import logger
except Exception:  # pragma: no cover - allow standalone import in tests
    import logging

    logger = logging.getLogger(__name__)


class LossResultSyncError(RuntimeError):
    """失敗結果 UI 尚未同步完成；呼叫端必須保留現場並停止所有後續動作。"""


# --- 節點路徑（5556 實測，全部 emit 有效）-------------------------------------
BTN_HOME_START = "/UIRoot/NormalView/RogueView/view/btnStart"            # 主面板「開始」
BTN_ENTER_START = "/UIRoot/NormalView/RogueEnterView/bg/btn"            # 選起點「開始」
BTN_MSG_ENSURE = "/UIRoot/TopView/MessageView/boxTips/dialog/content/buttons/btnEnsure"  # 確認窗「確定」
BTN_REMAKE_ENTER = "/UIRoot/NormalView/RogueRemakeRewardView/view/btnEnter"  # 開局獎勵「進入遊戲」
BTN_STAGE_START = "/UIRoot/NormalView/RogueMainView/view/btnStart"      # 「開始挑戰」(穿過 Block)
BTN_STAGE_EXIT = "/UIRoot/NormalView/RogueMainView/view/btnClose"       # 右下紅箭頭
BTN_ENDTIPS_END = "/UIRoot/NormalView/RogueEndTipsView/btn1"            # 「結束本局」
BTN_RESULT_CLOSE = "/UIRoot/NormalView/RogueBattleResultView/imgMask"   # 結果窗「點擊任意位置關閉」的 catcher(實測 root 無效，須 imgMask)
BTN_SETTLE_GOODS = "/UIRoot/NormalView/GoodsGetView/Block"              # 結算獎勵「點擊空白處關閉」
BTN_REPORT_CLOSE = "/UIRoot/NormalView/RogueRecordInfoView/view/btnClose"  # 本局報告 ✕
# 神樹祝福(RogueScienceView) — 讀「本周獲取上限 cur/cap」，用來決定要刷幾局(取代寫死次數)
BTN_SCIENCE = "/UIRoot/NormalView/RogueView/view/btnScience"            # 主面板「神樹祝福」
SCIENCE_LIMIT = "/UIRoot/NormalView/RogueScienceView/content/costTips/limit"  # RichText「本周獲取上限：cur/cap」
BTN_SCIENCE_CLOSE = "/UIRoot/NormalView/RogueScienceView/content/btnClose"

# --- 時間常數（節點/WS 路徑很快，戰鬥結果 server-authoritative 但 client 有動畫）------
_PACE = 1.2                 # 每個動作後基本間隔(秒)，避免 tight-loop 打 WS
_STATE_POLL = 1.0           # 輪詢狀態間隔
_ADVANCE_TIMEOUT = 40       # 從主面板進到關卡視圖上限
_BATTLE_TIMEOUT = 90        # 單關等結果上限(慢速裝置戰鬥動畫)
_SETTLE_TIMEOUT = 40        # 結算退出回主面板上限
_MAX_STAGES = 80            # 單局安全上限關數
# 結算「回到主面板」需連續穩定，避免獎勵/本局報告延遲彈出造成假 HOME
# （2026-08-04 log：settle 成功後 1s 內開下一局 → state=SETTLE 空等逾時）
_HOME_STABLE_POLLS = 3      # 連續幾次讀到 HOME 才算真回主面板
_HOME_STABLE_GAP = 0.5      # 穩定檢查間隔(秒)
_ACTION_RETRY_SEC = 5.0     # 同一畫面轉場未完成時，避免重複送出同一 click


# --- cocos JS -----------------------------------------------------------------
_STATE_JS = r"""
() => {
  const scene = cc.director.getScene();
  const find = (name) => {
    let hit = null;
    const w = (n) => { if (hit) return; if ((n.name||'') === name) { hit = n; return; }
      (n.children||[]).forEach(w); };
    w(scene); return hit;
  };
  const active = (name) => { const n = find(name); return !!(n && n.active); };
  const childActive = (parentName, childName) => {
    const p = find(parentName); if (!p) return false;
    const c = (p.children||[]).find(x => (x.name||'') === childName);
    return !!(c && c.active);
  };
  // 確認窗訊號：boxTips.active（實測開=true/關=false 才可信；txtContent 字串關閉後殘留 stale，
  // 只拿來 log，不拿來判斷）。用精確路徑避免抓到同名節點。
  const byPath = (p) => { const t = '/' + p.split('/').filter(Boolean).join('/'); let h = null;
    const ww = (n, pp) => { if (h) return; if (pp === t) { h = n; return; }
      (n.children||[]).forEach(c => ww(c, pp + '/' + (c.name||'?'))); };
    ww(scene, ''); return h; };
  const box = byPath('/UIRoot/TopView/MessageView/boxTips');
  const boxActive = !!(box && box.active);
  let confirmText = '';
  if (boxActive) {
    const tc = byPath('/UIRoot/TopView/MessageView/boxTips/dialog/content/txtContent');
    const lab = tc && (tc._components||[]).map(c=>c&&(c.string??c._string)).find(s=>typeof s==='string'&&s.trim());
    confirmText = lab || '';
  }
  return {
    rogueView: active('RogueView'),
    mainView: active('RogueMainView'),
    enterView: active('RogueEnterView'),
    remakeView: active('RogueRemakeRewardView'),
    goodsStartView: active('RogueGoodsGetView'),   // 開局遮罩(與 mainView 並存, emit 開始挑戰可繞)
    resultView: active('RogueBattleResultView'),
    win: childActive('RogueBattleResultView', 'nodeWin'),
    lose: childActive('RogueBattleResultView', 'nodeDefeat'),
    endTips: active('RogueEndTipsView'),
    recordView: active('RogueRecordInfoView'),
    settleGoods: active('GoodsGetView'),
    confirm: boxActive,
    confirmText: confirmText,
  };
}
"""

_EMIT_JS = r"""
(path) => {
  const target = '/' + path.split('/').filter(Boolean).join('/');
  let n = null;
  const w = (node, pp) => { if (n) return; if (pp === target) { n = node; return; }
    (node.children||[]).forEach(c => w(c, pp + '/' + (c.name||'?'))); };
  w(cc.director.getScene(), '');
  if (!n) return {found:false};
  const res = {found:true, active:n.active, tried:[]};
  try { n.emit('click', n); res.tried.push('emit'); } catch(e){ res.emit_err=String(e); }
  try {
    const b = n.getComponent && n.getComponent('cc.Button');
    if (b && b.clickEvents && b.clickEvents.length) {
      for (const ev of b.clickEvents) {
        const comp = ev.target && ev.target.getComponent(ev._componentName || ev.component);
        if (comp && typeof comp[ev.handler] === 'function') { comp[ev.handler](n, ev.customEventData); res.tried.push('ce:'+ev.handler); }
      }
    }
  } catch(e){ res.ce_err = String(e); }
  return res;
}
"""


# 狀態列舉（字串，KISS）
HOME, ENTER, CONFIRM, REMAKE, STAGE = "HOME", "ENTER", "CONFIRM", "REMAKE", "STAGE"
RESULT_WIN, RESULT_LOSE, END_TIPS = "RESULT_WIN", "RESULT_LOSE", "END_TIPS"
SETTLE, UNKNOWN = "SETTLE", "UNKNOWN"


def classify(s: dict) -> str:
    """把 read_state() 的 flag dict 分類成單一狀態（優先序：越上層/越阻斷越先）。"""
    if not isinstance(s, dict):
        return UNKNOWN
    if s.get("resultView"):
        if s.get("lose"):
            return RESULT_LOSE
        if s.get("win"):
            return RESULT_WIN
        return RESULT_WIN  # 結果窗開著但勝敗未定 → 當贏續判(close 後重讀)
    if s.get("confirm"):   # boxTips.active(確認窗開著)；rogue 各確認窗都按確定
        return CONFIRM
    if s.get("recordView") or s.get("settleGoods"):
        return SETTLE
    if s.get("endTips"):
        return END_TIPS
    # REMAKE 必須排在 STAGE 前：開局獎勵 RogueRemakeRewardView(有「進入遊戲」按鈕) 會與
    # RogueMainView 並存(mainView 提早 active)，若先判 STAGE 會對著 RemakeReward 亂點開始挑戰。
    # 反之 RogueGoodsGetView(純 Block 遮罩、無按鈕) 不算 REMAKE，emit 開始挑戰可直接繞過 → 仍 STAGE。
    if s.get("remakeView"):
        return REMAKE
    if s.get("mainView"):          # 關卡視圖(開局遮罩 goodsStartView 可能並存，emit 開始挑戰會繞過)
        return STAGE
    if s.get("enterView"):
        return ENTER
    if s.get("rogueView"):
        return HOME
    return UNKNOWN


def read_state(page: Any) -> dict:
    """讀 cocos 場景 → flag dict。判斷唯一資料來源(此 seam 未來可換成 WS)。"""
    try:
        return page.evaluate(_STATE_JS) or {}
    except Exception as e:
        logger.warning("[rogue_h5] read_state 失敗: %s", e)
        return {}


def state(page: Any) -> str:
    return classify(read_state(page))


def emit(page: Any, path: str) -> bool:
    try:
        r = page.evaluate(_EMIT_JS, path)
    except Exception as e:
        logger.warning("[rogue_h5] emit 例外 %s: %s", path, e)
        return False
    if not r or not r.get("found"):
        logger.warning("[rogue_h5] emit 找不到節點: %s", path)
        return False
    return True


def open_home(page: Any, timeout: float = 8.0) -> bool:
    """直接開 RogueView(萬神試煉主面板)，跳過『副本』清單捲動 + OCR 找入口。

    2026-07-21 live 驗證(5554)：`uiMgr.openView('RogueView')` 直接進主面板。
    OCR 路徑因副本清單變長(萬神試煉被推到第 5 頁)整批失效，H5 改走這條；
    ADB 沒有 uiMgr，仍走 OCR。
    """
    from utils.cocos_view import open_view

    return open_view(page, "RogueView", timeout=timeout)


_READTEXT_JS = r"""
(path) => {
  const target = '/' + path.split('/').filter(Boolean).join('/');
  let n = null;
  const w = (node, pp) => { if (n) return; if (pp === target) { n = node; return; }
    (node.children||[]).forEach(c => w(c, pp + '/' + (c.name||'?'))); };
  w(cc.director.getScene(), '');
  if (!n) return null;
  const s = (n._components||[]).map(c => c && (c.string ?? c._string)).find(x => typeof x === 'string');
  return s || null;
}
"""


def _read_text(page: Any, path: str) -> Optional[str]:
    try:
        return page.evaluate(_READTEXT_JS, path)
    except Exception as e:
        logger.warning("[rogue_h5] _read_text 例外 %s: %s", path, e)
        return None


def _parse_cap(raw: Optional[str]) -> Optional[tuple[int, int]]:
    """從 RichText『本周獲取上限： 5000/5000』字串解析 (cur, cap)。純函式(供測試)。

    先去 RichText 標籤——color 內含 #hex 可能有數字(如 #ca1414 的 14/14)，不去會誤抓。
    """
    import re
    if not raw:
        return None
    clean = re.sub(r"<[^>]*>", "", raw)
    m = re.search(r"(\d+)\s*/\s*(\d+)", clean)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def read_blessing_cap(page: Any) -> Optional[tuple[int, int]]:
    """從 RogueView 主面板讀『神樹祝福 → 本周獲取上限 cur/cap』。回傳 (cur, cap) 或 None。

    會開啟再關閉 RogueScienceView；呼叫時需在主面板(HOME)。用來把「刷幾局」由寫死次數
    改成「刷到本周上限為止」(見 run_rounds 的 until_cap)。**僅 H5(cocos 場景可讀)**；
    ADB 無場景樹讀不到，維持固定 rounds。
    """
    if not emit(page, BTN_SCIENCE):
        return None
    time.sleep(_PACE)
    raw = _read_text(page, SCIENCE_LIMIT)
    emit(page, BTN_SCIENCE_CLOSE)
    time.sleep(_PACE)
    cap = _parse_cap(raw)
    if cap is None:
        logger.warning("[rogue_h5] 讀不到/無法解析本周獲取上限: %r", raw)
    return cap


def _shot(cb: Optional[Callable[[str], None]], tag: str) -> None:
    if cb:
        try:
            cb(tag)
        except Exception:
            pass


def _active_flags(s: dict) -> dict:
    """只留 truthy flag，方便 log 診斷 SETTLE/UNKNOWN。"""
    if not isinstance(s, dict):
        return {}
    return {k: v for k, v in s.items() if v}


def _dismiss_settle_overlay(page: Any, s: dict) -> bool:
    """關掉結算獎勵 / 本局報告。回傳是否有發出關閉動作。"""
    if s.get("settleGoods"):
        return emit(page, BTN_SETTLE_GOODS)
    if s.get("recordView"):
        return emit(page, BTN_REPORT_CLOSE)
    return False


# 進場狀態機：狀態 → 動作。SETTLE/結果窗必須可關閉，不能只 wait。
# （2026-08-04：多裝置 log 在「上一局 settle 後再開下一局」卡 SETTLE 空等 40s）
_ADVANCE_EMIT = {
    HOME: BTN_HOME_START,
    ENTER: BTN_ENTER_START,
    CONFIRM: BTN_MSG_ENSURE,
    REMAKE: BTN_REMAKE_ENTER,
    RESULT_WIN: BTN_RESULT_CLOSE,
    RESULT_LOSE: BTN_RESULT_CLOSE,
    END_TIPS: BTN_ENDTIPS_END,  # 殘留「結束本局」對話框先往結算確認走
}


def advance_to_stage(page: Any, shot: Optional[Callable] = None) -> bool:
    """從主面板/任意開局窗，以狀態機逐步前進直到關卡視圖(STAGE)。

    每個 tick：read_state → classify → 依狀態表 emit/關閉覆蓋層 → pace。
    與舊版差異：SETTLE（本局報告/結算獎勵）會主動關閉，不再無動作空等逾時。
    """
    deadline = time.monotonic() + _ADVANCE_TIMEOUT
    last_action_key = None
    last_action_at = 0.0
    while time.monotonic() < deadline:
        s = read_state(page)
        st = classify(s)
        if st == STAGE:
            logger.info("[rogue_h5] 已到關卡視圖")
            _shot(shot, "stage")
            return True

        if st == SETTLE:
            logger.info("[rogue_h5] 進場 state=SETTLE → 關閉覆蓋層 flags=%s", _active_flags(s))
            if not _dismiss_settle_overlay(page, s):
                logger.info("[rogue_h5] 進場 SETTLE 無可用關閉鈕，等待 flags=%s", _active_flags(s))
        elif st in _ADVANCE_EMIT:
            act = _ADVANCE_EMIT[st]
            # CONFIRM 可能連續出現不同對話框，文字不同時視為新動作；同一對話框則等待
            # server/UI 完成轉場，避免每 1.2 秒重複送出確定或進場。
            action_key = (st, act, s.get("confirmText") if st == CONFIRM else None)
            now = time.monotonic()
            if action_key != last_action_key or now - last_action_at >= _ACTION_RETRY_SEC:
                logger.info("[rogue_h5] 進場 state=%s → emit", st)
                emit(page, act)
                last_action_key = action_key
                last_action_at = now
            else:
                logger.debug("[rogue_h5] 進場 state=%s 轉場中，略過重複 emit", st)
        else:
            # UNKNOWN 等：打 flags 方便對 live 場景
            logger.info("[rogue_h5] 進場 state=%s 等待 flags=%s", st, _active_flags(s))
        time.sleep(_PACE)
    logger.warning("[rogue_h5] 進場逾時，未到關卡視圖")
    return False


def _wait_result(page: Any) -> str:
    """點下開始挑戰後，等 RESULT_WIN / RESULT_LOSE；逾時回 UNKNOWN。"""
    deadline = time.monotonic() + _BATTLE_TIMEOUT
    while time.monotonic() < deadline:
        st = state(page)
        if st in (RESULT_WIN, RESULT_LOSE):
            return st
        time.sleep(_STATE_POLL)
    return UNKNOWN


def wait_and_close_loss_result(page: Any, timeout: float = _BATTLE_TIMEOUT) -> bool:
    """local_sim 算出失敗後，等失敗彈窗真正出現並確認關閉。

    失敗結果的 UI 可能比模擬結果晚數秒 render；在它出現前送「立即結算」會讓
    RogueBattleResultView 延遲疊到結算畫面上。等不到或關不掉時回 False，呼叫端
    必須停止，不可繼續送結算動作。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = state(page)
        if st == RESULT_LOSE:
            if not emit(page, BTN_RESULT_CLOSE):
                time.sleep(_STATE_POLL)
                continue
            while time.monotonic() < deadline:
                post_state = state(page)
                if post_state == STAGE:
                    logger.info("[rogue_h5][local_sim] 失敗彈窗已關閉並回到可結算關卡頁")
                    return True
                if post_state != RESULT_LOSE:
                    logger.warning(
                        "[rogue_h5][local_sim] 失敗彈窗關閉後狀態異常，等待 STAGE: %s",
                        post_state,
                    )
                time.sleep(_STATE_POLL)
            break
        time.sleep(_STATE_POLL)
    logger.warning("[rogue_h5][local_sim] 等待或關閉失敗彈窗逾時")
    return False


def battle_loop(
    page: Any,
    shot: Optional[Callable] = None,
    mode: str = "animation",
    ip: str = "",
) -> int:
    """單局：連續 開始挑戰 → 等結果 → 關結果，打到第一次失敗為止。回傳完成關數。

    mode="animation"（預設）：emit 開始挑戰 → _wait_result 輪詢最多 _BATTLE_TIMEOUT=90 秒。
    mode="local_sim"       ：install_hooks → clear_combat → emit 開始挑戰 →
                             run_sim_path(本頁 BattleMainServer 秒算 + send result) →
                             由 sim["result"] 判斷勝敗，無需等動畫；
                             sim 失敗(ok=False)自動 fallback 回 _wait_result。
                             runner.run_sim_path 的 finally 保證 set_block_result(False)，
                             所有路徑（含例外）都會解除攔截，不會永久吞官方 result。

    sim["result"] 語意（來源 simulate.py SIM_JS）：
      0 = 攻方（我方 playerList[1]）勝；非 0（通常 1）= 失敗。
      和競技場邏輯一致：`wid = (0 === result) ? atk.id : defRole.id`。
      rogue result_body_from_sim 直接回 {"result": int, "precent": int}。
      2026-07-17 小寶 CDP 9226 live 5 次驗證：deterministic 且對齊官方 result。

    關結果窗(BTN_RESULT_CLOSE)：local_sim 送完 result 後 client 仍會收 rogue_main_result_s2c
    並 render RogueBattleResultView；不點關閉會卡住下一關流程。需保留。
    """
    fought = 0

    # local_sim 預備：install_hooks 只需一次（冪等，重呼叫 JS 直接 return 'already'）
    if mode == "local_sim":
        try:
            from battle_calc.page_hooks import clear_combat, install_hooks
            from battle_calc.runner import run_sim_path
        except ImportError as e:
            logger.warning("[rogue_h5] battle_calc 載入失敗 → fallback animation: %s", e)
            mode = "animation"

    for _ in range(_MAX_STAGES):
        st = state(page)
        if st != STAGE:
            # 結果窗殘留/轉場中，先收掉再判
            if st in (RESULT_WIN, RESULT_LOSE):
                emit(page, BTN_RESULT_CLOSE)
                time.sleep(_PACE)
                if st == RESULT_LOSE:
                    return fought
                continue
            if st in (END_TIPS, SETTLE, HOME):  # 已離開關卡視圖
                return fought
            time.sleep(_PACE)
            continue

        if mode == "local_sim":
            # ── local_sim 路徑 ────────────────────────────────────────────
            # 順序：clear（清掉舊 combat） → emit 開始挑戰 → run_sim_path
            # install_hooks 在迴圈外已呼叫；clear_first=False 因為 emit 前才 clear 過。
            # run_sim_path finally 保證 set_block_result(False)，不需外層再清。
            install_hooks(page)
            clear_combat(page, "rogue")
            if not emit(page, BTN_STAGE_START):
                return fought
            fought += 1
            logger.info("[rogue_h5][local_sim] 第 %d 關 開始挑戰", fought)
            out = run_sim_path(
                page, "rogue", "local_sim",
                ip=ip, timeout_s=25.0, clear_first=False,
            )
            if out.get("ok"):
                sim = out.get("sim", {})
                # result=0 → 攻方（我方）勝；非 0 → 失敗
                won = int(sim.get("result", 1)) == 0
                precent = sim.get("precent", 0)
                ms = sim.get("ms", 0)
                res = RESULT_WIN if won else RESULT_LOSE
                logger.info(
                    "[rogue_h5][local_sim] 第 %d 關 sim ok ms=%.1f result=%s precent=%s",
                    fought, ms, sim.get("result"), precent,
                )
                _shot(shot, f"stage{fought}_{res}")
                if not won:
                    # 失敗彈窗可能延遲數秒才 render；必須等它出現並確實關閉，才能結算。
                    if not wait_and_close_loss_result(page):
                        raise LossResultSyncError("萬神失敗彈窗未完成，停止後續結算")
                    logger.info("[rogue_h5][local_sim] 第 %d 關 失敗 → 本局結束", fought)
                    return fought
                # 成功維持快速路徑：短暫讓 view render 後直接關閉；若尚未出現，下一輪
                # 既有狀態機遇到 RESULT_WIN 時仍會補關，不為成功結果額外阻塞等待。
                time.sleep(0.4)
                emit(page, BTN_RESULT_CLOSE)
                time.sleep(_PACE)
                continue
            else:
                # sim 失敗：block 已由 run_sim_path finally 釋放；fallback _wait_result
                logger.warning(
                    "[rogue_h5][local_sim] 第 %d 關 sim 失敗(%s) → fallback animation",
                    fought, out.get("err"),
                )
                # 走 animation 路徑收這關結果
                res = _wait_result(page)
                _shot(shot, f"stage{fought}_{res}_fallback")
                emit(page, BTN_RESULT_CLOSE)
                time.sleep(_PACE)
                if res in (RESULT_LOSE, UNKNOWN):
                    logger.info(
                        "[rogue_h5][local_sim] fallback 第 %d 關 %s → 本局結束", fought, res
                    )
                    return fought
                continue

        # ── animation 路徑（預設）────────────────────────────────────────
        if not emit(page, BTN_STAGE_START):     # 開始挑戰(穿過 Block)
            return fought
        fought += 1
        logger.info("[rogue_h5] 第 %d 關 開始挑戰", fought)
        res = _wait_result(page)
        _shot(shot, f"stage{fought}_{res}")
        emit(page, BTN_RESULT_CLOSE)            # 關結果窗(勝敗皆點)
        time.sleep(_PACE)
        if res == RESULT_LOSE:
            logger.info("[rogue_h5] 第 %d 關 失敗 → 本局結束", fought)
            return fought
        if res == UNKNOWN:
            logger.warning("[rogue_h5] 第 %d 關 等結果逾時 → 中止本局", fought)
            return fought
    return fought


def settle_run(page: Any, shot: Optional[Callable] = None) -> bool:
    """結束本局並回 RogueView 主面板（結算狀態機）。

    階段：
      1) OPEN_END_TIPS  — 紅箭頭 → 等 END_TIPS
      2) CONFIRM_END    — 「結束本局」→ 等 CONFIRM → 確定
      3) DISMISS        — 關獎勵/本局報告，直到 *穩定* HOME

    穩定 HOME：連續 _HOME_STABLE_POLLS 次 classify==HOME。
    避免「確定後短暫 HOME → 延遲報告/獎勵才跳出」被誤判成功，
    進而在下一局 advance 卡在 SETTLE（2026-08-04 多裝置實測）。
    """
    # --- 階段 1：開結束本局對話框 ---
    emit(page, BTN_STAGE_EXIT)
    if not _wait_state(page, {END_TIPS}, 10):
        logger.warning("[rogue_h5] 結算：未開出結束本局對話框")
        return False

    # --- 階段 2：確認結束本局 ---
    emit(page, BTN_ENDTIPS_END)                 # 結束本局
    if not _wait_state(page, {CONFIRM}, 10):
        logger.warning("[rogue_h5] 結算：未出結算確認窗")
        return False
    emit(page, BTN_MSG_ENSURE)                  # 確定「是否確認結算本局」
    _shot(shot, "settled")

    # --- 階段 3：關閉覆蓋層，穩定回到主面板 ---
    deadline = time.monotonic() + _SETTLE_TIMEOUT
    home_hits = 0
    while time.monotonic() < deadline:
        s = read_state(page)
        st = classify(s)
        if st == HOME:
            home_hits += 1
            if home_hits >= _HOME_STABLE_POLLS:
                logger.info("[rogue_h5] 本局結束，回到主面板(stable=%d)", home_hits)
                return True
            time.sleep(_HOME_STABLE_GAP)
            continue

        # 離開 HOME 就重置穩定計數
        home_hits = 0
        if st == SETTLE or s.get("settleGoods") or s.get("recordView"):
            logger.info("[rogue_h5] 結算 state=%s → 關閉覆蓋層 flags=%s", st, _active_flags(s))
            if not _dismiss_settle_overlay(page, s):
                logger.info("[rogue_h5] 結算覆蓋層無關閉鈕，等待 flags=%s", _active_flags(s))
        elif st in (RESULT_WIN, RESULT_LOSE):
            logger.info("[rogue_h5] 結算殘留結果窗 → 關閉")
            emit(page, BTN_RESULT_CLOSE)
        elif st == END_TIPS:
            logger.info("[rogue_h5] 結算再遇 END_TIPS → 結束本局")
            emit(page, BTN_ENDTIPS_END)
        elif st == CONFIRM:
            logger.info("[rogue_h5] 結算再遇 CONFIRM → 確定")
            emit(page, BTN_MSG_ENSURE)
        else:
            logger.info("[rogue_h5] 結算 state=%s 等待 flags=%s", st, _active_flags(s))
        time.sleep(_PACE)

    final = state(page)
    logger.warning("[rogue_h5] 結算後未確認回主面板(final=%s)", final)
    # 逾時後的單次 HOME 不代表穩定，否則仍可能漏掉稍後彈出的結算覆蓋層。
    return False


def _wait_state(page: Any, targets: set, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state(page) in targets:
            return True
        time.sleep(_STATE_POLL)
    return False


def run_rounds(
    page: Any,
    rounds: int = 8,
    shot: Optional[Callable] = None,
    until_cap: bool = False,
    mode: str = "animation",
    ip: str = "",
) -> int:
    """從 RogueView 主面板跑局(每局打到第一次失敗→結束本局)。回傳完成局數。

    - until_cap=False：跑滿 rounds 局(舊行為)。
    - until_cap=True：改讀『神樹祝福→本周獲取上限 cur/cap』，刷到 cur>=cap(達標) 或
      上一局無進度(cur 沒增加) 為止；`rounds` 退化成**安全上限**避免無限迴圈。
    - mode：傳給 battle_loop；"animation"(預設)=等動畫，"local_sim"=本頁秒算，
      "remote_calc"=遠端B；詳見 battle_loop docstring。

    呼叫前需已在萬神試煉主面板(RogueView)；副本清單→入場的導航仍由 weekly_trials 負責。
    """
    completed = 0
    last_cur: Optional[int] = None
    for r in range(rounds):
        if until_cap:
            cap = read_blessing_cap(page)          # 在主面板讀(開/關神樹祝福)
            if cap:
                cur, mx = cap
                logger.info("[rogue_h5] 本周獲取上限 %d/%d", cur, mx)
                if cur >= mx:
                    logger.info("[rogue_h5] 已達本周上限 → 停止(已完成 %d 局)", completed)
                    break
                if last_cur is not None and cur <= last_cur:
                    logger.info("[rogue_h5] 上一局無進度(%d→%d) → 停止", last_cur, cur)
                    break
                last_cur = cur
            else:
                logger.warning("[rogue_h5] 讀不到上限 → 退回用 rounds 安全上限續跑")
        logger.info("[rogue_h5] === 第 %d 局 (安全上限 %d) ===", r + 1, rounds)
        if not advance_to_stage(page, shot):
            logger.warning("[rogue_h5] 第 %d 局 無法進入關卡視圖 → 停止", r + 1)
            break
        fought = battle_loop(page, shot, mode=mode, ip=ip)
        logger.info("[rogue_h5] 第 %d 局 完成 %d 關", r + 1, fought)
        if not settle_run(page, shot):
            logger.warning("[rogue_h5] 第 %d 局 結算退出失敗 → 停止", r + 1)
            break
        completed += 1
    logger.info("[rogue_h5] 結束：完成 %d 局(until_cap=%s)", completed, until_cap)
    return completed


def fight(
    d: Any,
    rounds: int = 8,
    shot: Optional[Callable] = None,
    until_cap: bool = False,
    mode: str = "animation",
) -> int:
    """weekly_trials 用的薄包裝：由 device 取 playwright page 後跑 run_rounds。

    mode 傳給 run_rounds → battle_loop；預設 animation（向後兼容）。
    ip 從 device_id 取，用於 battle_calc 日誌。
    """
    page = getattr(d, "_page", None)
    if page is None:
        logger.warning("[rogue_h5] device 無 _page(非 web_h5 或 session 未起) → 放棄")
        return 0
    ip = str(getattr(d, "device_id", "") or "")
    return run_rounds(page, rounds=rounds, shot=shot, until_cap=until_cap, mode=mode, ip=ip)

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

# --- 時間常數（節點/WS 路徑很快，戰鬥結果 server-authoritative 但 client 有動畫）------
_PACE = 1.2                 # 每個動作後基本間隔(秒)，避免 tight-loop 打 WS
_STATE_POLL = 1.0           # 輪詢狀態間隔
_ADVANCE_TIMEOUT = 40       # 從主面板進到關卡視圖上限
_BATTLE_TIMEOUT = 90        # 單關等結果上限(慢速裝置戰鬥動畫)
_SETTLE_TIMEOUT = 40        # 結算退出回主面板上限
_MAX_STAGES = 80            # 單局安全上限關數


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


def _shot(cb: Optional[Callable[[str], None]], tag: str) -> None:
    if cb:
        try:
            cb(tag)
        except Exception:
            pass


def advance_to_stage(page: Any, shot: Optional[Callable] = None) -> bool:
    """從主面板/任意開局窗，逐步 emit 直到進入關卡視圖(STAGE)。"""
    deadline = time.monotonic() + _ADVANCE_TIMEOUT
    while time.monotonic() < deadline:
        st = state(page)
        if st == STAGE:
            logger.info("[rogue_h5] 已到關卡視圖")
            _shot(shot, "stage")
            return True
        act = {
            HOME: BTN_HOME_START,
            ENTER: BTN_ENTER_START,
            CONFIRM: BTN_MSG_ENSURE,
            REMAKE: BTN_REMAKE_ENTER,
        }.get(st)
        if act:
            logger.info("[rogue_h5] 進場 state=%s → emit", st)
            emit(page, act)
        else:
            logger.info("[rogue_h5] 進場 state=%s 等待", st)
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


def battle_loop(page: Any, shot: Optional[Callable] = None) -> int:
    """單局：連續 開始挑戰 → 等結果 → 關結果，打到第一次失敗為止。回傳完成關數。"""
    fought = 0
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
    """結束本局並回 RogueView 主面板：紅箭頭 → 結束本局 → 確定 → 關獎勵/報告。回傳是否回到主面板。"""
    # 開結束本局對話框
    emit(page, BTN_STAGE_EXIT)
    if not _wait_state(page, {END_TIPS}, 10):
        logger.warning("[rogue_h5] 結算：未開出結束本局對話框")
        return False
    emit(page, BTN_ENDTIPS_END)                 # 結束本局
    if not _wait_state(page, {CONFIRM}, 10):
        logger.warning("[rogue_h5] 結算：未出結算確認窗")
        return False
    emit(page, BTN_MSG_ENSURE)                  # 確定「是否確認結算本局」
    _shot(shot, "settled")
    # 關結算後的獎勵 + 本局報告，逐步驗證回到主面板
    deadline = time.monotonic() + _SETTLE_TIMEOUT
    while time.monotonic() < deadline:
        s = read_state(page)
        st = classify(s)
        if st == HOME:
            logger.info("[rogue_h5] 本局結束，回到主面板")
            return True
        if s.get("settleGoods"):
            emit(page, BTN_SETTLE_GOODS)
        elif s.get("recordView"):
            emit(page, BTN_REPORT_CLOSE)
        time.sleep(_PACE)
    logger.warning("[rogue_h5] 結算後未確認回主面板")
    return state(page) == HOME


def _wait_state(page: Any, targets: set, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if state(page) in targets:
            return True
        time.sleep(_STATE_POLL)
    return False


def run_rounds(page: Any, rounds: int = 8, shot: Optional[Callable] = None) -> int:
    """從 RogueView 主面板跑滿 rounds 局(每局打到第一次失敗→結束本局)。回傳完成局數。

    呼叫前需已在萬神試煉主面板(RogueView)；副本清單→入場的導航仍由 weekly_trials 負責。
    """
    completed = 0
    for r in range(rounds):
        logger.info("[rogue_h5] === 第 %d/%d 局 ===", r + 1, rounds)
        if not advance_to_stage(page, shot):
            logger.warning("[rogue_h5] 第 %d 局 無法進入關卡視圖 → 停止", r + 1)
            break
        fought = battle_loop(page, shot)
        logger.info("[rogue_h5] 第 %d/%d 局 完成 %d 關", r + 1, rounds, fought)
        if not settle_run(page, shot):
            logger.warning("[rogue_h5] 第 %d 局 結算退出失敗 → 停止", r + 1)
            break
        completed += 1
    logger.info("[rogue_h5] 結束：完成 %d/%d 局", completed, rounds)
    return completed


def fight(d: Any, rounds: int = 8, shot: Optional[Callable] = None) -> int:
    """weekly_trials 用的薄包裝：由 device 取 playwright page 後跑 run_rounds。"""
    page = getattr(d, "_page", None)
    if page is None:
        logger.warning("[rogue_h5] device 無 _page(非 web_h5 或 session 未起) → 放棄")
        return 0
    return run_rounds(page, rounds=rounds, shot=shot)

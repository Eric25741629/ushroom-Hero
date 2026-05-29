"""開神燈「啟動清殘留」狀態機（純邏輯，無 UI / 截圖）。

開神燈進入燈介面時，上一輪若沒正確收尾，會殘留以下狀態之一，必須先清乾淨
再開燈，否則盲點固定座標會打在殘留視窗上 → 導航全錯：

  1. 全部出售 grid（20 件等級不符待賣）   → SELL_ALL
  2. 「當前裝備 vs NEW」單件待處理         → COMPARISON
  3. 一般彈窗（離線獎勵 / 恭喜獲得）        → BLOCKING_POPUP
  4. 乾淨主頁（尚未進燈介面）              → MAIN（點入燈介面）
  5. 燈介面就緒（無殘留）                  → READY（可開燈）

COMPARISON（單件）的處理必須走「同一套規則」：先自動檢查開出的組合是不是「要的」，
不要的 → 出售；要的 → 切換方案、移動到對應的方案、再比較 → 裝備/出售。
（即既有 `_process_wanted_combo`，不可用簡化版的兩列比較取代。）

本模組只負責「給我目前畫面分類，告訴你下一步動作」，並用 stall / 迭代上限避免
卡在賣不掉 / 關不掉的死迴圈。實際偵測與點擊由 UIController / LampService 負責。
與 `lamp_loop_state.py`（開燈「進行中」的穩定偵測）職責不同。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class LampEntryState(Enum):
    """進入燈介面後（或主頁）目前畫面屬於哪一種啟動狀態。"""

    SELL_ALL = "sell_all"               # 全部出售 grid
    COMPARISON = "comparison"           # 當前 vs NEW 單件待處理
    BLOCKING_POPUP = "blocking_popup"   # 離線獎勵 / 恭喜獲得 等一般彈窗
    MAIN = "main"                       # 乾淨主頁（尚未進燈介面）
    READY = "ready"                     # 燈介面就緒，無殘留
    UNKNOWN = "unknown"


class StartupAction(Enum):
    """resolver 要求外層執行的動作。"""

    SELL_ALL = "sell_all"                   # 點全部出售 → 確認 → 驗證清空
    PROCESS_OPENED_EQUIP = "process_opened_equip"  # 走同一套規則：檢查是否要的→切方案→移動→比較→裝備/出售
    DISMISS_POPUP = "dismiss_popup"         # 關閉一般彈窗
    ENTER_LAMP = "enter_lamp"               # 從主頁點入燈介面
    DONE = "done"                           # 已就緒，可開燈
    ABORT = "abort"                         # stall / 超過上限，放棄本輪


def classify_entry_state(
    *,
    sell_all: bool,
    comparison: bool,
    blocking_popup: bool,
    main: bool,
) -> LampEntryState:
    """依偵測結果分類，優先序：強制 modal（賣/單件）> 一般彈窗 > 主頁 > 就緒。

    全部出售與單件比較窗是「強制 modal」（Escape 關不掉，會擋住一切），必須最優先處理；
    都沒有且不在主頁時，視為燈介面就緒（READY）。樂觀判定 READY 沒問題 — 開燈主迴圈
    （LampLoopState + banner→renav）是後續的安全網。
    """
    if sell_all:
        return LampEntryState.SELL_ALL
    if comparison:
        return LampEntryState.COMPARISON
    if blocking_popup:
        return LampEntryState.BLOCKING_POPUP
    if main:
        return LampEntryState.MAIN
    return LampEntryState.READY


class StartupResolver:
    """驅動「清殘留」迴圈：每 tick 餵入分類狀態，回傳下一步動作。

    - max_iters: 總迭代上限（含 popup 連環跳之類會變化的情況）。
    - max_stall: 同一個未解決狀態連續出現幾次就 ABORT（賣不掉 / 關不掉的死迴圈防護）。
    """

    def __init__(self, max_iters: int = 12, max_stall: int = 3) -> None:
        self.max_iters = max_iters
        self.max_stall = max_stall
        self.iters = 0
        self._last_state: Optional[LampEntryState] = None
        self._stall = 0

    _ACTION_BY_STATE = {
        LampEntryState.SELL_ALL: StartupAction.SELL_ALL,
        LampEntryState.COMPARISON: StartupAction.PROCESS_OPENED_EQUIP,
        LampEntryState.BLOCKING_POPUP: StartupAction.DISMISS_POPUP,
        LampEntryState.MAIN: StartupAction.ENTER_LAMP,
        LampEntryState.READY: StartupAction.DONE,
    }

    def next_action(self, state: LampEntryState) -> StartupAction:
        self.iters += 1
        if self.iters > self.max_iters:
            return StartupAction.ABORT

        if state == self._last_state:
            self._stall += 1
        else:
            self._stall = 1
        self._last_state = state

        # READY 是終止態，不受 stall 影響
        if state is not LampEntryState.READY and self._stall >= self.max_stall:
            return StartupAction.ABORT

        return self._ACTION_BY_STATE.get(state, StartupAction.ABORT)

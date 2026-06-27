"""萬神試煉Beta (roguelike / RogueView) — 進場 → 打到不能打 → 每週祕寶閣購買。

2026-06-20 重寫：遊戲已把舊「萬神試煉」改成 roguelike「萬神試煉Beta」，舊版 7 場
(開始/確定/結束本局/每輪秘寶閣) UI 已不存在。本版走 CDP live 實測驗證的流程
(5554 暖啟『繼續』/ 小寶 7fe98fc6 冷啟『開始』+ 多層確認窗)。
週積分獎勵由 WS rogue 週五領取(ws_token.runner._run_rogue)，本版只負責真打 + 祕寶閣。
全程用內建 img_tools.click_str_by_server / check_str_in_region(OCR 子字串比對)。
"""

import time

import img_tools
from utils.logging_utils import logger

from ._helpers import _recover_to_home
from .store import buy_god_everyweek

# 副本清單『萬神試煉』文字 → 該列『入場』鈕的偏移 (實測 540x960：文字中心+(277,75)≈入場鈕)
_ENTER_SHIFT = (277, 75)
# 進場確認窗輪點優先序：確定(確認窗蓋在按鈕上→最先點) > 進入遊戲 > 繼續 > 開始 > 點擊(獎勵 toast)。
# 注意：每步先檢查『開始挑戰』(已到關卡視圖)才點；且『開始挑戰』含『開始』，故不可把開始排在偵測前。
_ENTRY_BUTTONS = ("確定", "進入遊戲", "繼續", "開始", "點擊")
_ENTRY_MAX_STEPS = 14
_BATTLE_MAX_STAGES = 80       # 安全上限；rogue 一局有限(終會失敗或次數用盡)
_BATTLE_SETTLE_TRIES = 9      # 每關等結算輪詢次數 (~2s/次 ≈ 18s)
_RUN_MAX_SECONDS = 15 * 60    # 單輪戰鬥迴圈上限 15 分鐘(使用者 2026-06-21)


def _advance_to_stage(d) -> bool:
    """從萬神主面板/任意確認窗，輪點確認窗直到出現『開始挑戰』(關卡視圖)。"""
    for step in range(_ENTRY_MAX_STEPS):
        if img_tools.check_str_in_region(d, "開始挑戰"):
            logger.info("[萬神試煉] 已到關卡視圖 (step %d)", step)
            return True
        clicked = next(
            (kw for kw in _ENTRY_BUTTONS if img_tools.click_str_by_server(d, kw)),
            None,
        )
        if clicked is None:
            logger.warning("[萬神試煉] 進場第 %d 步無可點按鈕，停止", step)
            return False
        logger.info("[萬神試煉] 進場 step%d 點『%s』", step, clicked)
        time.sleep(2.0)
    logger.warning("[萬神試煉] 進場 %d 步仍未到關卡視圖", _ENTRY_MAX_STEPS)
    return False


def _battle_loop(d, max_stages: int = _BATTLE_MAX_STAGES) -> int:
    """連續 開始挑戰 → 等結果彈窗 → 點擊關閉。回傳完成關卡數。

    停止條件(任一)：
    1. 偵測到『失敗』(過濾器；本局結束)
    2. 找不到『開始挑戰』(次數用盡 / 已離開關卡視圖)
    3. 單輪超過 15 分鐘(_RUN_MAX_SECONDS 上限)
    4. 達 max_stages 安全上限
    """
    fought = 0
    start = time.monotonic()
    for _ in range(max_stages):
        if time.monotonic() - start > _RUN_MAX_SECONDS:
            logger.info("[萬神試煉] 達單輪 15 分鐘上限 → 停止 (已 %d 關)", fought)
            break
        if not img_tools.click_str_by_server(d, "開始挑戰"):
            logger.info("[萬神試煉] 找不到『開始挑戰』→ 本局結束/不能打 (已 %d 關)", fought)
            break
        fought += 1
        logger.info("[萬神試煉] 第 %d 關 開始挑戰", fought)
        # 戰鬥由 client 自動跑；出現『點擊…關閉』提示即代表結果窗已出(勝敗同一種窗)
        for _ in range(_BATTLE_SETTLE_TRIES):
            time.sleep(2.0)
            if img_tools.check_str_in_region(d, "點擊"):
                break
        else:
            logger.warning("[萬神試煉] 第 %d 關等結果窗逾時，仍嘗試關閉續判", fought)
        lost = img_tools.check_str_in_region(d, "失敗")  # 失敗過濾器
        img_tools.click_str_by_server(d, "點擊")          # 關掉結果窗(勝敗皆點)
        time.sleep(1.5)
        if lost:
            logger.info("[萬神試煉] 第 %d 關 偵測到『失敗』→ 本局結束", fought)
            break
    return fought


def fight_test(d, max_stages: int = _BATTLE_MAX_STAGES) -> bool:
    """萬神試煉Beta：進副本 → 入場 → 打到不能打 → 每週祕寶閣購買。

    回傳是否「實際打了至少一關」，供排程決定要不要寫本週記錄(避免失敗也鎖一週)。
    max_stages 僅供測試限關用；排程呼叫不帶參數(=打到不能打)。
    """
    logger.info("[萬神試煉] 開始：點『副本』")
    img_tools.click_str_by_server(d, "副本")
    time.sleep(2)
    # rogue 結算 / 秘寶閣面板不會自動回主頁；不主動返回的話，本輪後續任務
    # (雲端戰鬥/好友禮物/開神燈/轉盤金幣) 會全部因「不在主頁面」被跳過。
    # 進『副本』後一律在收尾返回主頁（成功與中止路徑皆然）。
    try:
        # 副本清單找『萬神試煉』(Beta)：命中文字+偏移=該列入場鈕；找不到就上滑捲動再試
        entered = False
        for _ in range(4):
            if img_tools.click_str_by_server(
                d, "萬神試煉", shift_x=_ENTER_SHIFT[0], shift_y=_ENTER_SHIFT[1]
            ):
                entered = True
                break
            d.swipe(239, 600, 239, 300, 0.2)
            time.sleep(1)
        if not entered:
            logger.warning("[萬神試煉] 副本清單找不到『萬神試煉』入口 → 中止(未挑戰)")
            return False
        time.sleep(2)

        if not _advance_to_stage(d):
            logger.warning("[萬神試煉] 無法進入關卡視圖 → 中止")
            return False

        fought = _battle_loop(d, max_stages=max_stages)
        logger.info("[萬神試煉] 戰鬥結束，共完成 %d 關", fought)

        try:
            buy_god_everyweek(d)  # 每週祕寶閣購買(週積分由 WS rogue 週五領)
        except Exception:
            logger.exception("[萬神試煉] 祕寶閣購買流程異常")

        return fought > 0
    finally:
        # ponytail: 單次 best-effort 回主頁；殘留面板時下次對齊喚醒由 detector 重啟恢復。
        _recover_to_home(d)

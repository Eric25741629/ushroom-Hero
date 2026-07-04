"""萬神試煉Beta (roguelike / RogueView) — 進副本 → 跑 N 局(每局打到第一次失敗→結束本局)→ 每週祕寶閣。

2026-06-29 重寫(live recon 5554，見 docs/ROGUE_WANSHEN_BETA_AUTOMATION.md §9)：
rogue 一次失敗只扣 1「試煉之心」、可續打、❤=0 才真結束;舊版 `_battle_loop` 偵測到「失敗」
就 break + 寫週記錄 = 「假完成」(只打一局就鎖一週)。使用者定案:
  一局 = 開始 → 爬到第一次失敗 → 「結束本局」(退出) → 重進;跑滿 N 局(預設 8、可調
  `wanshen_rounds`)才算完成 → 才寫週記錄。

流程(全程內建 `img_tools.click_str_by_server` / `check_str_in_region` OCR 子字串比對):
  點副本 → 萬神試煉 入場(到 RogueView 主面板)
  for _ in range(rounds):
      _advance_to_stage  # 輪點 開始/繼續→確定(確認窗)→點空白(開局獎勵) 直到「開始挑戰」
      _battle_loop       # 連打到第一次「失敗」/逾時/「開始挑戰」消失
      _settle_run        # 右下紅箭頭(座標)→結束本局→確定(確認窗)→關結算窗 回主面板
  buy_god_everyweek      # 每週祕寶閣(週積分由 WS rogue 週五領)
週積分獎勵由 WS rogue 週五領取(ws_token.runner._run_rogue)，本版只負責真打 + 祕寶閣。
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
# 確認窗「是否確認開啟新一局試煉」掛在 TopView/MessageView，OCR 看得到「確定」(不分 cocos 層)。
_ENTRY_BUTTONS = ("確定", "進入遊戲", "繼續", "開始", "點擊")
_ENTRY_MAX_STEPS = 14
_DEFAULT_ROUNDS = 8           # 每週開局(局)數;一局=爬到第一次失敗→結束本局(使用者 2026-06-29 定案、可調)
_BATTLE_MAX_STAGES = 80       # 單局安全上限關數;rogue 一局有限(終會失敗或逾時)
_BATTLE_SETTLE_TRIES = 9      # 每關等結算輪詢次數 (~2s/次 ≈ 18s)
_RUN_MAX_SECONDS = 15 * 60    # 單局戰鬥迴圈上限 15 分鐘(使用者 2026-06-21)
# 退出/結算座標(540x960 邏輯座標;OCR 會把紅箭頭誤判成 'G' 故用座標)。實測值，必要時 live 微調。
_EXIT_ARROW_XY = (510, 920)   # 右下紅色離開箭頭(RogueMainView/view/btnClose)
# 『結束本局』按鈕座標(btn1):OCR 子字串「結束本局」會誤命中說明文字「選擇暫時離開或結束本局」
# (中央 ~(269,433)),故按鈕用座標點。2026-06-30 live 實測 btn1 css=(168,522)。
_END_RUN_BTN_XY = (168, 522)
_REPORT_CLOSE_XY = (270, 875) # 本局報告 RogueRecordInfoView 的 ✕(無文字，OCR 點不到;2026-06-30 實測)
_DIALOG_WAIT = 4.5            # RogueEndTipsView / 結算確認窗 轉場等待(2026-06-21 實測需 4-5s)
_SETTLE_DISMISS_TRIES = 8     # 結算後關掉「本局報告 + 獎勵」窗的嘗試次數
# RogueView 主面板辨識字(可開新局):這些在主面板有、關卡視圖/RogueEnterView 沒有。
_ROGUE_HOME_MARKERS = ("神樹祝福", "結算倒計時")
# 結算/報告/獎勵/關卡 覆蓋層 marker:任一在 = 還有窗沒關,不算回到主面板。
# (主面板底部按鈕「神樹祝福」會在報告蓋不到處透出 → 不能只靠它判 home,2026-06-30 假陽性教訓)
_ROGUE_OVERLAY_MARKERS = ("開始挑戰", "試煉時間", "抵達關卡", "恭喜", "結束本局")


def _advance_to_stage(d) -> bool:
    """從萬神主面板/任意確認窗，輪點確認窗直到出現『開始挑戰』(關卡視圖)。

    首局由副本『入場』進來、後續各局由上一局『結束本局』後的主面板進來，皆走同一輪點:
    開始/繼續 → 確認窗『確定』→ 開局獎勵『點擊空白』→ 關卡視圖。
    """
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
    """單局：連續 開始挑戰 → 等結果彈窗 → 點擊關閉，打到第一次『失敗』為止。回傳完成關卡數。

    停止條件(任一)：
    1. 偵測到『失敗』(本局打到第一次敗北 → 該局結束，交給 _settle_run 退出)
    2. 找不到『開始挑戰』(已離開關卡視圖 / 異常)
    3. 單局超過 15 分鐘(_RUN_MAX_SECONDS;帳號太強連勝不止時的保底)
    4. 達 max_stages 安全上限
    """
    fought = 0
    start = time.monotonic()
    for _ in range(max_stages):
        if time.monotonic() - start > _RUN_MAX_SECONDS:
            logger.info("[萬神試煉] 達單局 15 分鐘上限 → 停止 (已 %d 關)", fought)
            break
        if not img_tools.click_str_by_server(d, "開始挑戰"):
            logger.info("[萬神試煉] 找不到『開始挑戰』→ 本局結束/不能打 (已 %d 關)", fought)
            break
        fought += 1
        logger.info("[萬神試煉] 第 %d 關 開始挑戰", fought)
        # 戰鬥由 client 自動跑；出現『點擊…關閉』提示即代表結果窗已出(勝敗同一種窗，只差橫幅顏色)
        for _ in range(_BATTLE_SETTLE_TRIES):
            time.sleep(2.0)
            if img_tools.check_str_in_region(d, "點擊"):
                break
        else:
            logger.warning("[萬神試煉] 第 %d 關等結果窗逾時，仍嘗試關閉續判", fought)
        lost = img_tools.check_str_in_region(d, "失敗")  # 失敗過濾器(藍色失敗橫幅)
        img_tools.click_str_by_server(d, "點擊")          # 關掉結果窗(勝敗皆點)
        time.sleep(1.5)
        if lost:
            logger.info("[萬神試煉] 第 %d 關 偵測到『失敗』→ 本局結束(將結束本局退出)", fought)
            break
    return fought


def _at_rogue_home(d) -> bool:
    """是否在 RogueView 主面板(可開新局):無任何覆蓋層(關卡/報告/獎勵) 且 有主面板辨識字。

    注意:主面板底部『神樹祝福』會在報告蓋不到處透出,故**必須先排除覆蓋層 marker**,
    不能只看『有神樹祝福、無開始挑戰』就判 home(2026-06-30 假陽性 → settle 沒關報告就回報成功)。
    """
    if any(img_tools.check_str_in_region(d, kw) for kw in _ROGUE_OVERLAY_MARKERS):
        return False  # 還有關卡/報告/獎勵窗
    return any(img_tools.check_str_in_region(d, kw) for kw in _ROGUE_HOME_MARKERS)


def _settle_run(d) -> bool:
    """結束本局並回 RogueView 主面板:右下紅箭頭 → 『結束本局』→ 確認窗『確定』→ 關結算窗。

    回傳是否「確認回到主面板」(可開下一局)。**fail-safe**:只在偵測到結算窗(獎勵『點擊』/
    本局報告)時才點關閉,**絕不盲點座標**(舊版盲點 (270,875) 連點誤觸主面板『開始』又開新局,
    是 2026-06-30『沒有正常退出』的根因);無法確認回主面板就回 False,讓上層中止 + 回主頁。
    退出序/等待取自 2026-06-21/06-29 live recon(對話框轉場需 4-5s)。
    """
    d.click(*_EXIT_ARROW_XY)  # 右下紅箭頭(OCR 誤判成 'G'，用座標)
    time.sleep(_DIALOG_WAIT)
    if not img_tools.check_str_in_region(d, "結束本局"):
        logger.warning("[萬神試煉] 結算:紅箭頭未開出『結束本局』對話框 → 中止本局結算")
        return False
    # 用按鈕座標點(OCR 子字串會誤命中說明文字「選擇暫時離開或結束本局」中央 → 點不到按鈕)
    d.click(*_END_RUN_BTN_XY)
    time.sleep(_DIALOG_WAIT)
    if not img_tools.click_str_by_server(d, "確定"):  # 確認窗『是否確認結算本局』
        logger.warning("[萬神試煉] 結算:找不到『確定』(結算確認窗) → 中止")
        return False
    time.sleep(_DIALOG_WAIT)
    # 關結算窗(獎勵『點擊空白』+ 本局報告『✕』無文字)。逐步驗證:回到主面板即停;
    # 只在報告可見(『試煉之心』/『抵達關卡』)時才點 ✕ 座標 → 不會誤觸主面板。
    for _ in range(_SETTLE_DISMISS_TRIES):
        if _at_rogue_home(d):
            logger.info("[萬神試煉] 本局已結束,回到 RogueView 主面板")
            return True
        if img_tools.click_str_by_server(d, "點擊"):       # 獎勵『點擊空白處關閉』
            time.sleep(1.2)
        elif img_tools.check_str_in_region(d, "試煉之心") or img_tools.check_str_in_region(d, "抵達關卡"):
            d.click(*_REPORT_CLOSE_XY)                       # 僅當『本局報告』可見才點 ✕
            time.sleep(1.2)
        else:
            time.sleep(1.0)                                  # 轉場中,等一下再判,不盲點
    ok = _at_rogue_home(d)
    if not ok:
        logger.warning("[萬神試煉] 結算後未確認回 RogueView 主面板 → 視為結算失敗(中止,不誤開新局)")
    return ok


def fight_test(d, rounds: int = _DEFAULT_ROUNDS) -> bool:
    """萬神試煉Beta：進副本 → 入場 → 跑滿 rounds 局(每局打到第一次失敗→結束本局)→ 祕寶閣。

    回傳是否「跑滿 rounds 局」,供排程決定要不要寫本週記錄(跑滿才算完成;否則下次重試)。
    rounds 由 device config `wanshen_rounds` 帶入(預設 8);傳小值供測試。
    """
    logger.info("[萬神試煉] 開始：點『副本』(目標 %d 局)", rounds)
    img_tools.click_str_by_server(d, "副本")
    time.sleep(2)
    # rogue 結算 / 秘寶閣面板不會自動回主頁；不主動返回的話，本輪後續任務
    # (雲端戰鬥/好友禮物/開神燈/轉盤金幣) 會全部因「不在主頁面」被跳過。
    # 進『副本』後一律在收尾返回主頁（成功與中止路徑皆然）。
    aborted = False  # 入場/進場/結算失敗 → 收尾除 _recover_to_home 外再強制導回主頁
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
            aborted = True
            return False
        time.sleep(2)

        completed = 0
        for r in range(rounds):
            if not _advance_to_stage(d):
                logger.warning("[萬神試煉] 第 %d/%d 局無法進入關卡視圖 → 停止", r + 1, rounds)
                aborted = True
                break
            fought = _battle_loop(d)
            logger.info("[萬神試煉] 第 %d/%d 局完成 %d 關(打到失敗/逾時)", r + 1, rounds, fought)
            if not _settle_run(d):
                logger.warning("[萬神試煉] 第 %d/%d 局結算退出失敗 → 停止", r + 1, rounds)
                aborted = True
                break
            completed += 1
            logger.info("[萬神試煉] 已完成 %d/%d 局", completed, rounds)

        try:
            buy_god_everyweek(d)  # 每週祕寶閣購買(週積分由 WS rogue 週五領)
        except Exception:
            logger.exception("[萬神試煉] 祕寶閣購買流程異常")

        done = completed >= rounds
        logger.info("[萬神試煉] 結束：完成 %d/%d 局，跑滿=%s", completed, rounds, done)
        return done
    finally:
        # ponytail: 單次 best-effort 回主頁；殘留面板時下次對齊喚醒由 detector 重啟恢復。
        _recover_to_home(d)
        if aborted:
            # 中止路徑(入場失敗/進場失敗/結算退出失敗)：_recover_to_home 的盲點序列
            # 不保證離開 Rogue 殘留面板(5558 log 實測 23 次停在非主頁污染後續任務)，
            # 再疊一層共用導航 helper 強制回主頁。lazy import 避免 battle 套件
            # 在載入期拉進 game_state.detector 的重依賴。
            try:
                from game_actions.navigation import navigate_to_main_page
                navigate_to_main_page(d, label="weekly_trials")
            except Exception:
                logger.exception("[萬神試煉] 中止後導回主頁失敗")

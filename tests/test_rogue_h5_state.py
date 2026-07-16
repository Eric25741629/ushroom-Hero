"""rogue_h5.classify() 狀態機單元測試（純資料，不接真 device）。"""

from battle import rogue_h5 as r


def _blank(**over):
    s = {
        "rogueView": False, "mainView": False, "enterView": False,
        "remakeView": False, "goodsStartView": False, "resultView": False,
        "win": False, "lose": False, "endTips": False, "recordView": False,
        "settleGoods": False, "confirm": False, "confirmText": "",
    }
    s.update(over)
    return s


def test_home():
    assert r.classify(_blank(rogueView=True)) == r.HOME


def test_enter():
    assert r.classify(_blank(rogueView=True, enterView=True)) == r.ENTER


def test_remake():
    assert r.classify(_blank(rogueView=True, remakeView=True)) == r.REMAKE


def test_confirm_by_boxtips_active():
    # 確認窗訊號 = boxTips.active(confirm)；txtContent stale 不參與判斷。
    assert r.classify(_blank(confirm=True, confirmText="是否確認開啟新一局試煉")) == r.CONFIRM
    # 即使文字空(stale/未讀到)，boxTips.active 就算 CONFIRM
    assert r.classify(_blank(rogueView=True, enterView=True, confirm=True, confirmText="")) == r.CONFIRM
    # boxTips 關 → 不是 confirm
    assert r.classify(_blank(rogueView=True, enterView=True, confirm=False)) == r.ENTER


def test_stage_plain():
    assert r.classify(_blank(rogueView=True, mainView=True)) == r.STAGE


def test_stage_with_start_reward_mask():
    # 關鍵：開局獎勵遮罩 RogueGoodsGetView(純 Block) 與 RogueMainView 並存時仍判 STAGE
    # → 讓上層 emit 開始挑戰(穿過 Block)，不會卡在遮罩。
    s = _blank(rogueView=True, mainView=True, goodsStartView=True)
    assert r.classify(s) == r.STAGE


def test_remake_beats_stage():
    # RogueRemakeRewardView(進入遊戲) 與 RogueMainView 並存時要先判 REMAKE，
    # 否則會對著開局獎勵面板亂點開始挑戰(實測 5556 的根因)。
    s = _blank(rogueView=True, mainView=True, remakeView=True)
    assert r.classify(s) == r.REMAKE


def test_result_win_and_lose():
    assert r.classify(_blank(resultView=True, win=True)) == r.RESULT_WIN
    assert r.classify(_blank(resultView=True, lose=True)) == r.RESULT_LOSE
    # 結果窗開著但勝敗旗標都沒到 → 當 WIN 續判(close 後重讀)
    assert r.classify(_blank(resultView=True)) == r.RESULT_WIN


def test_result_beats_stage():
    # 結果窗優先於關卡視圖(戰後 mainView 可能仍 active)
    s = _blank(mainView=True, resultView=True, lose=True)
    assert r.classify(s) == r.RESULT_LOSE


def test_end_tips():
    assert r.classify(_blank(mainView=True, endTips=True)) == r.END_TIPS


def test_settle_report_or_goods():
    assert r.classify(_blank(recordView=True)) == r.SETTLE
    assert r.classify(_blank(settleGoods=True)) == r.SETTLE
    # 結算窗優先於底層 rogueView
    assert r.classify(_blank(rogueView=True, recordView=True)) == r.SETTLE


def test_unknown():
    assert r.classify(_blank()) == r.UNKNOWN
    assert r.classify(None) == r.UNKNOWN
    assert r.classify("garbage") == r.UNKNOWN

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


def test_parse_cap_richtext():
    # 實測 5556/小寶 的 RichText 原字串
    raw = "<b><color=#544231>本周獲取上限： <color=#ca1414>5000<color>/5000<color></b>"
    assert r._parse_cap(raw) == (5000, 5000)


def test_parse_cap_partial():
    assert r._parse_cap("本周獲取上限： 1234/5000") == (1234, 5000)
    # color hex 內的數字不可被誤抓(#544231/#ca1414)
    assert r._parse_cap("<color=#544231>本周獲取上限： <color=#00ff00>0<color>/5000") == (0, 5000)


def test_parse_cap_bad():
    assert r._parse_cap(None) is None
    assert r._parse_cap("") is None
    assert r._parse_cap("沒有數字") is None


class _FakePage:
    """假 page：emit 一律成功，_read_text 回固定上限字串。"""
    def __init__(self, limit_text):
        self.limit_text = limit_text
        self.states = []

    def evaluate(self, js, arg=None):
        if js == r._EMIT_JS:
            return {"found": True}
        if js == r._READTEXT_JS:
            return self.limit_text
        if js == r._STATE_JS:
            # 若真的進到跑局(不該發生)→回 UNKNOWN 讓它很快 timeout, 測試才不會誤過
            return {}
        return {}


def test_run_rounds_until_cap_stops_when_capped(monkeypatch):
    # 已達本周上限(5000/5000) → 不應開跑任何一局, completed=0
    monkeypatch.setattr(r, "_PACE", 0)
    page = _FakePage("本周獲取上限： 5000/5000")
    assert r.run_rounds(page, rounds=5, until_cap=True) == 0


def test_read_blessing_cap_via_fake_page(monkeypatch):
    monkeypatch.setattr(r, "_PACE", 0)
    page = _FakePage("<b>本周獲取上限： <color=#ca1414>1200<color>/5000</b>")
    assert r.read_blessing_cap(page) == (1200, 5000)


class _OpenViewPage:
    """假 page：第一次 evaluate 是 openView，之後都是 read_state。"""

    def __init__(self, *, open_err="", rogue_after=True):
        self.open_err = open_err
        self.rogue_after = rogue_after
        self.calls = []

    def evaluate(self, js, arg=None):
        self.calls.append(arg)
        if arg == "RogueView":
            return self.open_err
        return _blank(rogueView=self.rogue_after)


def test_open_home_jumps_without_dungeon_list():
    page = _OpenViewPage()
    assert r.open_home(page) is True
    assert page.calls[0] == "RogueView"  # 直接開 view，沒有捲清單


def test_open_home_fails_when_uimgr_missing():
    page = _OpenViewPage(open_err="no uiMgr.openView")
    assert r.open_home(page) is False


def test_open_home_fails_when_view_never_activates():
    page = _OpenViewPage(rogue_after=False)
    assert r.open_home(page, timeout=0.5) is False

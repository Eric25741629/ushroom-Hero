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
    """假 page：依 JS 內容分派 openView / active 檢查（對應 utils.cocos_view）。

    未呼叫 openView 前一律回報「沒開」，才能驗證 open_home 真的去開了 view。
    """

    def __init__(self, *, open_err="", opens_ok=True):
        self.open_err = open_err
        self.opens_ok = opens_ok
        self.opened = []

    def evaluate(self, js, arg=None):
        if "openView" in js and "activeInHierarchy" not in js:
            self.opened.append(arg)
            return self.open_err
        return bool(self.opened) and self.opens_ok and not self.open_err


def test_open_home_jumps_without_dungeon_list():
    page = _OpenViewPage()
    assert r.open_home(page) is True
    assert page.opened == ["RogueView"]  # 直接開 view，沒有捲清單


def test_open_home_fails_when_uimgr_missing():
    page = _OpenViewPage(open_err="no uiMgr.openView")
    assert r.open_home(page) is False


def test_open_home_fails_when_view_never_activates():
    page = _OpenViewPage(opens_ok=False)
    assert r.open_home(page, timeout=0.5) is False


def test_open_view_skips_when_already_open():
    from utils import cocos_view

    page = _OpenViewPage()
    page.opened.append("pre")  # 假裝已經開著
    assert cocos_view.open_view(page, "DoubleChapterMainView") is True
    assert page.opened == ["pre"]  # 沒有重複 openView


# --- 進場/結算狀態機（2026-08-04：SETTLE 殘窗 + 過早 HOME）-----------------

def _flags(**over):
    return _blank(**over)


class _FsmPage:
    """依目前 flags 回 read_state；emit 時依路徑推進，模擬 cocos UI 轉場。"""

    def __init__(self, initial):
        self.flags = dict(initial)
        self.emits = []
        self._after_ensure_seq = None
        self._after_ensure_idx = 0
        self._ensure_armed = False

    def set_after_ensure_sequence(self, seq):
        self._after_ensure_seq = [dict(x) for x in seq]
        self._after_ensure_idx = 0

    def evaluate(self, js, arg=None):
        if js == r._STATE_JS:
            if (
                self._ensure_armed
                and self._after_ensure_seq is not None
                and self._after_ensure_idx < len(self._after_ensure_seq)
            ):
                self.flags = dict(self._after_ensure_seq[self._after_ensure_idx])
                self._after_ensure_idx += 1
            return dict(self.flags)
        if js == r._EMIT_JS:
            self.emits.append(arg)
            self._on_emit(arg)
            return {"found": True, "active": True, "tried": ["emit"]}
        return {}

    def _on_emit(self, path):
        raise NotImplementedError


class _AdvancePage(_FsmPage):
    def _on_emit(self, path):
        if path == r.BTN_HOME_START:
            # 點開始後先冒出延遲本局報告（真實 log 竞態）
            self.flags = _flags(rogueView=True, recordView=True)
        elif path == r.BTN_REPORT_CLOSE:
            self.flags = _flags(rogueView=True, enterView=True)
        elif path == r.BTN_SETTLE_GOODS:
            self.flags = _flags(rogueView=True, enterView=True)
        elif path == r.BTN_ENTER_START:
            self.flags = _flags(confirm=True, confirmText="是否確認開啟新一局試煉")
        elif path == r.BTN_MSG_ENSURE:
            self.flags = _flags(rogueView=True, mainView=True)
        elif path == r.BTN_REMAKE_ENTER:
            self.flags = _flags(rogueView=True, mainView=True)
        elif path == r.BTN_RESULT_CLOSE:
            self.flags = _flags(rogueView=True, mainView=True)


def test_advance_to_stage_dismisses_delayed_settle_report(monkeypatch):
    """回歸：結算後點開始，延遲本局報告蓋住進場 → 必須關掉再進 STAGE。"""
    monkeypatch.setattr(r, "_PACE", 0)
    monkeypatch.setattr(r, "_ADVANCE_TIMEOUT", 5)
    page = _AdvancePage(_flags(rogueView=True))
    assert r.advance_to_stage(page) is True
    assert r.BTN_HOME_START in page.emits
    assert r.BTN_REPORT_CLOSE in page.emits
    assert r.BTN_ENTER_START in page.emits
    assert r.BTN_MSG_ENSURE in page.emits


def test_advance_to_stage_dismisses_settle_goods(monkeypatch):
    """進場途中遇到 GoodsGetView 也要關，不能只 wait。"""
    monkeypatch.setattr(r, "_PACE", 0)
    monkeypatch.setattr(r, "_ADVANCE_TIMEOUT", 5)
    page = _AdvancePage(_flags(rogueView=True, settleGoods=True))
    assert r.advance_to_stage(page) is True
    assert r.BTN_SETTLE_GOODS in page.emits


def test_advance_to_stage_closes_result_then_reaches_stage(monkeypatch):
    """進場狀態機要收掉殘留結果窗再視為 STAGE。"""
    monkeypatch.setattr(r, "_PACE", 0)
    monkeypatch.setattr(r, "_ADVANCE_TIMEOUT", 5)
    page = _AdvancePage(_flags(rogueView=True, mainView=True, resultView=True, lose=True))
    assert r.advance_to_stage(page) is True
    assert r.BTN_RESULT_CLOSE in page.emits


class _SettlePage(_FsmPage):
    def _on_emit(self, path):
        if path == r.BTN_STAGE_EXIT:
            self.flags = _flags(rogueView=True, mainView=True, endTips=True)
        elif path == r.BTN_ENDTIPS_END:
            self.flags = _flags(confirm=True, confirmText="是否確認結算本局")
        elif path == r.BTN_MSG_ENSURE:
            # 只 armed；下一拍 read_state 再消費序列，避免漏掉第一幀 HOME
            self._ensure_armed = True
            self._after_ensure_idx = 0
            if not self._after_ensure_seq:
                self.flags = _flags(rogueView=True)
        elif path == r.BTN_REPORT_CLOSE:
            # 關報告後給穩定 HOME
            self._after_ensure_seq = [
                _flags(rogueView=True),
                _flags(rogueView=True),
                _flags(rogueView=True),
                _flags(rogueView=True),
            ]
            self._after_ensure_idx = 0
            self.flags = _flags(rogueView=True)
        elif path == r.BTN_SETTLE_GOODS:
            self.flags = _flags(rogueView=True)


def test_settle_run_stable_home_dismisses_late_report(monkeypatch):
    """回歸：確定後先閃 HOME 再出本局報告 → 不可提早成功，須關報告並穩定 HOME。"""
    monkeypatch.setattr(r, "_PACE", 0)
    monkeypatch.setattr(r, "_STATE_POLL", 0)
    monkeypatch.setattr(r, "_HOME_STABLE_GAP", 0)
    monkeypatch.setattr(r, "_HOME_STABLE_POLLS", 3)
    monkeypatch.setattr(r, "_SETTLE_TIMEOUT", 5)

    page = _SettlePage(_flags(rogueView=True, mainView=True))
    page.set_after_ensure_sequence([
        _flags(rogueView=True),                         # 過早 HOME
        _flags(rogueView=True),                         # 第 2 次 HOME（未滿 3）
        _flags(rogueView=True, recordView=True),        # 延遲報告
        _flags(rogueView=True),
        _flags(rogueView=True),
        _flags(rogueView=True),
    ])
    assert r.settle_run(page) is True
    assert r.BTN_REPORT_CLOSE in page.emits


def test_settle_run_rejects_unstable_home(monkeypatch):
    """HOME 無法穩定維持時應結算失敗，避免誤開下一局。"""
    monkeypatch.setattr(r, "_PACE", 0)
    monkeypatch.setattr(r, "_STATE_POLL", 0)
    monkeypatch.setattr(r, "_HOME_STABLE_GAP", 0)
    monkeypatch.setattr(r, "_HOME_STABLE_POLLS", 3)
    monkeypatch.setattr(r, "_SETTLE_TIMEOUT", 1)

    page = _SettlePage(_flags(rogueView=True, mainView=True))
    page.set_after_ensure_sequence([
        _flags(rogueView=True),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
        _flags(),
    ])
    assert r.settle_run(page) is False


def test_settle_run_timeout_rejects_single_final_home(monkeypatch):
    """逾時後最後一拍 HOME 未達穩定次數，不可誤判結算成功。"""
    monkeypatch.setattr(r, "_SETTLE_TIMEOUT", 1)
    monkeypatch.setattr(r, "_wait_state", lambda *_args, **_kwargs: True)
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(r.time, "monotonic", lambda: next(ticks))

    page = _SettlePage(_flags(rogueView=True, mainView=True))
    page.set_after_ensure_sequence([_flags(rogueView=True)])

    assert r.settle_run(page) is False

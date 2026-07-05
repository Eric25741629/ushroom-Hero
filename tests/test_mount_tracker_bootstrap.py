"""冷啟 bootstrap（bootstrap_known）純函式測試（全依賴注入，不碰真 socket / sleep / 時鐘）。

FAKE caller 依 cmd + 解碼後的請求 body 路由，回傳以 ws_token.codec 自組的 canned bytes：
- 兩頁公會搜尋：page0 有 level>=5 公會、page1 全 level<5 → 驗證提早收 type、page1 的公會不被查成員。
- 合格公會的成員列表 + role_others 回等級 → 驗證 seed 對到 {name, guild, level}。
"""
from ws_token import codec
from ws_token import mount_scan as ms

import runtime_services.mount_tracker_service as mt


# --- canned-bytes 組裝工具（以 codec 反向組出 parser 吃的 body）----------------

def _gs_page(page_num, guilds):
    """公會搜尋頁：page_num(#3) + 每個公會一個 #4(entry)。guilds=[(gid, name, lvl, mem)]。"""
    body = codec.pb_uint(3, page_num)
    for gid, name, lvl, mem in guilds:
        entry = (codec.pb_uint(1, gid) + codec.pb_str(3, name)
                 + codec.pb_uint(8, lvl) + codec.pb_uint(10, mem))
        body += codec.pb_msg(4, entry)
    return body


def _gm(members):
    """公會成員列表：每個成員一個 top #2(role_id(#1) + name(#2))。"""
    body = b""
    for rid, name in members:
        body += codec.pb_msg(2, codec.pb_uint(1, rid) + codec.pb_str(2, name))
    return body


def _ro(levels):
    """role_others：{role_id: level} → top #1(role_id(#1) + info(#2)(attr 1001=level))。"""
    body = b""
    for rid, lvl in levels.items():
        info = codec.pb_msg(1, codec.pb_uint(1, 1001) + codec.pb_uint(2, lvl))
        body += codec.pb_msg(1, codec.pb_uint(1, rid) + codec.pb_msg(2, info))
    return body


def _make_caller(calls):
    """依 cmd + 請求內容路由的 FAKE caller；把成員 / 等級查詢記進 calls。"""
    def caller(cmd, body):
        d = codec.walk_dict(body)
        if cmd == ms.CMD_GUILD_SEARCH:
            typ, page = d.get(1), d.get(3)
            if typ == 2 and page == 0:
                # 有一個 level 15 合格公會 + 一個 level 4 不合格公會。
                return _gs_page(2, [(900, "羽皇居", 15, 81), (902, "小小", 4, 6)])
            if typ == 2 and page == 1:
                # 整頁 level<5 → 非空頁 0 筆合格 → 提早收 type 2。
                return _gs_page(2, [(901, "路人會", 3, 5)])
            # typ 3：直接空頁（page_num=1）→ page>=page_num-1 立即收。
            return _gs_page(1, [])
        if cmd == ms.CMD_GUILD_MEMBERS:
            gid = d.get(1)
            calls["members"].append(gid)
            if gid == 900:
                return _gm([(555, "曇花一現"), (556, "月下")])
            return _gm([])
        if cmd == ms.CMD_ROLE_OTHERS:
            ids = [v for f, v in codec.walk(body) if f == 1]
            calls["levels"].append(ids)
            return _ro({555: 200, 556: 88})
        return None
    return caller


# --- 正常路徑 ----------------------------------------------------------------

def test_bootstrap_known_seeds_name_guild_level_and_early_stops():
    calls = {"members": [], "levels": []}
    sleeps = []
    caller = _make_caller(calls)

    seed = mt.bootstrap_known(caller, sleeps.append, lambda: 0.0, lambda: True,
                              deadline=1e9)

    # 合格公會 900 的兩名成員，帶 name/guild/level。
    assert seed == {
        555: {"name": "曇花一現", "guild": "羽皇居", "level": 200},
        556: {"name": "月下", "guild": "羽皇居", "level": 88},
    }
    # 提早收 type：只查了合格公會 900 的成員；level<5 的 901/902 不查。
    assert calls["members"] == [900]
    # 等級批次查詢一次涵蓋兩人。
    assert calls["levels"] == [[555, 556]]
    # 每次網路呼叫前都有冷卻（sleeper 被呼叫過）。
    assert sleeps and all(s == 3.0 for s in sleeps)


def test_bootstrap_known_none_response_skips_page():
    """公會搜尋回 None 的頁被跳過，不影響最終 seed。"""
    calls = {"members": [], "levels": []}
    base = _make_caller(calls)
    state = {"first": True}

    def flaky(cmd, body):
        # 第一次公會搜尋回 None（該頁跳過），之後照常。
        if cmd == ms.CMD_GUILD_SEARCH and state["first"]:
            state["first"] = False
            return None
        return base(cmd, body)

    seed = mt.bootstrap_known(flaky, lambda s: None, lambda: 0.0, lambda: True,
                              deadline=1e9)
    # None 頁被跳過（不崩、不誤把該頁當作提早收 type 的空頁）；低等公會 901/902 一律不查成員。
    assert isinstance(seed, dict)
    assert 901 not in calls["members"] and 902 not in calls["members"]


# --- 早停條件 ----------------------------------------------------------------

def test_should_continue_false_returns_empty():
    calls = {"members": [], "levels": []}
    seed = mt.bootstrap_known(_make_caller(calls), lambda s: None, lambda: 0.0,
                              lambda: False, deadline=1e9)
    assert seed == {}
    assert calls["members"] == [] and calls["levels"] == []


def test_deadline_in_past_returns_empty():
    calls = {"members": [], "levels": []}
    seed = mt.bootstrap_known(_make_caller(calls), lambda s: None, lambda: 100.0,
                              lambda: True, deadline=0.0)
    assert seed == {}
    assert calls["members"] == [] and calls["levels"] == []


# --- 即時進度回報 ------------------------------------------------------------

def test_bootstrap_known_reports_guilds_members_known_progress():
    """on_progress：階段1 回報 guilds、階段2 回報 members、階段3 回報 known。"""
    calls = {"members": [], "levels": []}
    events = []
    mt.bootstrap_known(_make_caller(calls), lambda s: None, lambda: 0.0,
                       lambda: True, deadline=1e9,
                       on_progress=lambda **kw: events.append(kw))

    keys = set().union(*[set(e) for e in events]) if events else set()
    assert {"guilds", "members", "known"} <= keys      # 三階段都有回報

    guild_vals = [e["guilds"] for e in events if "guilds" in e]
    assert guild_vals and max(guild_vals) == 1          # 合格公會只有 900
    member_vals = [e["members"] for e in events if "members" in e]
    assert member_vals and max(member_vals) == 2        # 公會 900 的兩名成員
    known_vals = [e["known"] for e in events if "known" in e]
    assert known_vals and max(known_vals) == 2          # 階段3 補等級後 known=2

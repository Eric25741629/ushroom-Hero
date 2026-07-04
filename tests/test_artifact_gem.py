"""ws_token/artifact_gem.py — 解析 / 防呆 / 分解 body 編碼 的單元測試。

不連 live socket：用 codec 合成一個 artifact_gem_info_s2c body 再餵 parser。
"""
from ws_token import artifact_gem as ag
from ws_token import codec


def _kv(k: int, v: int) -> bytes:
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _gem_entry(*, gid, quality=3, pos=1, suit=101, lv=1, is_lock=0,
               base=None) -> bytes:
    body = (codec.pb_uint(1, gid) + codec.pb_uint(2, quality)
            + codec.pb_uint(3, pos) + codec.pb_uint(4, suit)
            + codec.pb_uint(5, lv) + codec.pb_uint(8, is_lock))
    for attr_id, val in (base or {}).items():
        body += codec.pb_msg(9, _kv(attr_id, val))
    return body


def _tab_entry(tab: int, name: str, slots: dict[int, int]) -> bytes:
    """slots = {pos_id: gem_id}（v=0 視為空槽）。"""
    body = codec.pb_uint(1, tab) + codec.pb_str(2, name)
    for pos_id, gem_id in slots.items():
        body += codec.pb_msg(3, _kv(pos_id, gem_id))
    return body


def _info_body(gems: list[bytes], tab: int, tabs: list[bytes]) -> bytes:
    body = b""
    for g in gems:
        body += codec.pb_msg(2, g)
    body += codec.pb_uint(3, tab)
    for t in tabs:
        body += codec.pb_msg(4, t)
    return body


def test_parse_gem_info_fields_and_equipped_ids():
    # Arrange: 3 顆石；其中 100 號裝在 tab1/tab2、200 號未裝、300 號鎖定。
    g100 = _gem_entry(gid=100, quality=5, pos=2, suit=104, lv=9,
                      base={1001: 50, 1004: 7})
    g200 = _gem_entry(gid=200, quality=3, pos=4, suit=101, lv=2)
    g300 = _gem_entry(gid=300, quality=8, pos=6, suit=107, lv=1, is_lock=1)
    body = _info_body(
        [g100, g200, g300],
        tab=5,
        tabs=[_tab_entry(1, "打戰士", {1: 100, 2: 0}),
              _tab_entry(2, "打魚", {1: 100})],  # 100 跨 tab 共用
    )

    # Act
    inv = ag.parse_gem_info(body)

    # Assert
    assert inv.tab == 5
    assert len(inv.gems) == 3
    assert inv.equipped_ids == frozenset({100})  # v=0 的空槽被排除、跨 tab 去重
    g = {x.id: x for x in inv.gems}
    assert g[100].quality == 5 and g[100].pos == 2 and g[100].suit == 104
    assert g[100].lv == 9 and g[100].base_attr == {1001: 50, 1004: 7}
    assert g[300].is_lock is True


def test_select_safe_excludes_equipped_locked_missing():
    g100 = _gem_entry(gid=100, lv=9)
    g200 = _gem_entry(gid=200, lv=2)
    g300 = _gem_entry(gid=300, lv=1, is_lock=1)
    body = _info_body([g100, g200, g300], tab=1,
                      tabs=[_tab_entry(1, "x", {1: 100})])
    inv = ag.parse_gem_info(body)

    safe, blocked = ag.select_safe(inv, ["100", "200", "300", "999"])

    assert safe == [200]
    assert blocked == {100: "equipped", 300: "locked", 999: "not_found"}


def test_build_split_body_roundtrip_repeated_uint64():
    ids = [89555437489175, 200, 7]  # 含大 uint64
    body = ag.build_split_body(ids)
    got = [v for fn, v in codec.walk(body) if fn == 1]
    assert got == ids


def test_decompose_empty_is_noop():
    # 空清單不送 WS、直接回 not-ok，避免空 split。
    out = ag.decompose(client=None, ids=[])  # client 不會被碰
    assert out == {"ok": False, "error_code": 0, "removed": []}

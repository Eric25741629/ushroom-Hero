from ws_token import codec
from ws_token import mount_scan as ms


def test_enc_role_others_repeated_ids():
    body = ms.enc_role_others([111, 222], source=1)
    fields = codec.walk(body)
    assert [v for f, v in fields if f == 1] == [111, 222]
    assert any(f == 2 and v == 1 for f, v in fields)


def test_parse_guild_search_extracts_level_and_name():
    entry = codec.pb_uint(1, 900) + codec.pb_str(3, "羽皇居") + codec.pb_uint(8, 15) + codec.pb_uint(10, 81)
    body = codec.pb_uint(3, 90) + codec.pb_msg(4, entry)
    r = ms.parse_guild_search(body)
    assert r["page_num"] == 90
    g = r["guilds"][0]
    assert g["guild_id"] == 900 and g["name"] == "羽皇居" and g["guild_level"] == 15 and g["member_num"] == 81


def test_parse_guild_members():
    m = codec.pb_uint(1, 555) + codec.pb_str(2, "曇花一現")
    body = codec.pb_msg(2, m)
    assert ms.parse_guild_members(body) == [{"role_id": 555, "name": "曇花一現"}]


def test_parse_role_others_reads_level_server_power():
    kv = lambda a, v: codec.pb_msg(1, codec.pb_uint(1, a) + codec.pb_uint(2, v))
    info = kv(1001, 200) + kv(1006, 1467) + kv(1020, 999)
    entry = codec.pb_uint(1, 555) + codec.pb_msg(2, info)
    body = codec.pb_msg(1, entry)
    r = ms.parse_role_others(body)
    assert r[555] == {"level": 200, "server": 1467, "power": 999}


def test_parse_lot_occupants_reads_space_and_skins():
    space = codec.pb_uint(1, 3) + codec.pb_uint(2, 555) + codec.pb_uint(5, 1783267198) + codec.pb_str(9, "曇花一現")
    empty = codec.pb_uint(1, 1) + codec.pb_uint(2, 0)  # role_id 0 => empty, skipped
    skin = codec.pb_uint(1, 10002) + codec.pb_uint(2, 10)
    body = codec.pb_msg(7, space) + codec.pb_msg(7, empty) + codec.pb_msg(8, skin)
    r = ms.parse_lot_occupants(body)
    assert r["spaces"] == [{"pos": 3, "role_id": 555, "start_time": 1783267198, "name": "曇花一現"}]
    assert (10002, 10) in r["skin_list"]


def test_weight_prioritises_recent_host_then_guild():
    hi = ms.weight({"role_id": 1, "host_hits": 3, "coin": 100, "guild": "羽皇居", "level": 200}, {"羽皇居"})
    lo = ms.weight({"role_id": 2, "host_hits": 0, "coin": 100, "guild": "路人", "level": 200}, {"羽皇居"})
    assert hi > lo

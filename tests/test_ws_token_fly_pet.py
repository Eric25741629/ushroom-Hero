from ws_token import codec
from ws_token import fly_pet


def _kv(k: int, v: int) -> bytes:
    return codec.pb_uint(1, k) + codec.pb_uint(2, v)


def _pet(
    pet_id: int,
    *,
    config_id: int = 1001,
    quality: int = 5,
    fight: int = 0,
    lock: int = 0,
) -> bytes:
    body = (
        codec.pb_uint(1, pet_id)
        + codec.pb_uint(3, config_id)
        + codec.pb_uint(4, 30)
        + codec.pb_str(7, "測試飛寵")
        + codec.pb_uint(8, 2)
        + codec.pb_uint(9, fight)
        + codec.pb_uint(10, 12345)
        + codec.pb_uint(13, quality)
        + codec.pb_msg(15, _kv(301, 4))
        + codec.pb_msg(16, _kv(1, 1))
    )
    if lock:
        body += codec.pb_msg(16, _kv(2, lock))
    return body


def test_parse_pet_info_preserves_entries_lock_and_star():
    body = codec.pb_msg(1, _pet(101, lock=1))

    pets = fly_pet.parse_pet_info(body)

    assert len(pets) == 1
    pet = pets[0]
    assert pet.id == 101
    assert pet.config_id == 1001
    assert pet.name == "測試飛寵"
    assert pet.entries[0].id == 301
    assert pet.entries[0].level == 4
    assert pet.lock is True
    assert pet.star is True


def test_select_safe_resolve_ids_blocks_server_side_unsafe_pets():
    pets = [
        fly_pet.parse_pet(_pet(1)),
        fly_pet.parse_pet(_pet(2, lock=1)),
        fly_pet.parse_pet(_pet(3, config_id=3003)),
        fly_pet.parse_pet(_pet(4, fight=1)),
    ]

    safe, skipped = fly_pet.select_safe_resolve_ids(
        pets, requested_ids=[1, 2, 3, 4, 999], collected_config_ids={3003}
    )

    assert safe == [1]
    assert skipped == {
        "locked": 1,
        "collected": 1,
        "deployed": 1,
        "missing": 1,
    }


def test_build_repeated_uint_uses_unpacked_wire_values():
    body = fly_pet.build_resolve_body([101, 202])

    assert codec.walk(body) == [(1, 101), (1, 202)]


def test_parse_base_pet_uses_compact_schema_where_field_four_is_name():
    compact = (
        codec.pb_uint(1, 77)
        + codec.pb_uint(2, 88)
        + codec.pb_uint(3, 1002)
        + codec.pb_str(4, "親代")
        + codec.pb_msg(5, _kv(302, 3))
    )
    base = (
        codec.pb_uint(1, 2)
        + codec.pb_uint(3, 1)
        + codec.pb_msg(4, compact)
    )

    parsed = fly_pet.parse_base(base)

    assert parsed.fly_a.id == 77
    assert parsed.fly_a.name == "親代"
    assert parsed.fly_a.entries == (fly_pet.Entry(302, 3),)


class _FakeClient:
    def __init__(self, replies):
        self.replies = dict(replies)
        self.calls = []

    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.calls.append((cmd, body, tuple(expect_cmds)))
        return cmd, self.replies[cmd]


class _Bootstrap173Client(_FakeClient):
    def call_for(self, cmd, body=b"", *, expect_cmds, timeout=None):
        self.calls.append((cmd, body, tuple(expect_cmds)))
        if cmd == fly_pet.CMD_EGG_INFO:
            return fly_pet.CMD_ERROR, codec.pb_uint(1, 173)
        return cmd, self.replies[cmd]


def test_read_snapshot_uses_pure_ws_commands():
    client = _FakeClient({
        fly_pet.CMD_EGG_INFO: b"",
        fly_pet.CMD_PET_INFO: codec.pb_msg(1, _pet(11)),
        fly_pet.CMD_COLLECTION: codec.pb_uint(1, 1001),
    })

    snapshot = fly_pet.read_snapshot(client)

    assert [p.id for p in snapshot.pets] == [11]
    assert snapshot.collected_config_ids == {1001}
    assert [c[0] for c in client.calls] == [
        fly_pet.CMD_EGG_INFO,
        fly_pet.CMD_PET_INFO,
        fly_pet.CMD_COLLECTION,
    ]


def test_read_snapshot_tolerates_live_no_egg_bootstrap_173():
    client = _Bootstrap173Client({
        fly_pet.CMD_PET_INFO: codec.pb_msg(1, _pet(11)),
        fly_pet.CMD_COLLECTION: b"",
    })

    snapshot = fly_pet.read_snapshot(client)

    assert [p.id for p in snapshot.pets] == [11]


def test_start_breeding_encodes_all_three_ids():
    base = codec.pb_uint(1, 7) + codec.pb_uint(3, 1)
    client = _FakeClient({
        fly_pet.CMD_EGG_INFO: b"",
        fly_pet.CMD_HYBRID_START: codec.pb_msg(1, base),
    })

    result = fly_pet.start_breeding(client, 7, 111, 222)

    assert result.id == 7
    assert result.state == 1
    assert codec.walk(client.calls[1][1]) == [(1, 7), (2, 111), (3, 222)]

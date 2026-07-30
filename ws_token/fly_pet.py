"""飛寵純 WebSocket 協議層。

協議來源為 ``docs/protocol/FLYPET_PROTO_SCHEMA.json``。本模組只處理 protobuf
編解碼與 server RPC，不依賴 Playwright、CDP 或遊戲頁面內的 JS cache。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ws_token import codec


CMD_ERROR = 0x0201
CMD_EGG_INFO = 16897
CMD_PET_INFO = 16898
CMD_EGG_INCUBATE = 16899
CMD_PET_RESOLVE = 16904
CMD_HYBRID_BASE_INFO = 16917
CMD_HYBRID_SHELVES_INFO = 16918
CMD_HYBRID_SET_SHELVES = 16919
CMD_HYBRID_PARTNER_SHELVES = 16920
CMD_HYBRID_START = 16923
CMD_HYBRID_GET = 16924
CMD_COLLECTION = 16928


class FlyPetRPCError(RuntimeError):
    """飛寵 RPC 被 server 拒絕。"""

    def __init__(self, code: int, cmd: int):
        super().__init__(f"飛寵操作失敗（cmd={cmd}, code={code}）")
        self.code = int(code)
        self.cmd = int(cmd)


@dataclass(frozen=True)
class Entry:
    id: int
    level: int


@dataclass(frozen=True)
class Pet:
    id: int
    role_id: int = 0
    config_id: int = 0
    level: int = 0
    step: int = 0
    exp: int = 0
    name: str = ""
    generation: int = 0
    fight: int = 0
    growth: int = 0
    quality: int = 0
    entries: tuple[Entry, ...] = ()
    ext: dict[int, int] = field(default_factory=dict)

    @property
    def lock(self) -> bool:
        return bool(self.ext.get(2, 0))

    @property
    def star(self) -> bool:
        return bool(self.ext.get(1, 0))


@dataclass(frozen=True)
class Egg:
    id: int
    config_id: int = 0
    ext: dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Base:
    id: int
    name: str = ""
    state: int = 0
    fly_a: Pet | None = None
    fly_b: Pet | None = None
    start_time: int = 0
    end_time: int = 0


@dataclass(frozen=True)
class Use:
    id: int
    times: int = 0
    state: int = 0
    start_time: int = 0
    end_time: int = 0
    other_times: int = 0
    other_state: int = 0
    other_start_time: int = 0
    other_end_time: int = 0


@dataclass(frozen=True)
class Partner:
    role_id: int
    name: str = ""
    head: int = 0


@dataclass(frozen=True)
class Shelf:
    info: Pet
    is_save: int = 0
    state: int = 0
    start_time: int = 0
    end_time: int = 0
    use_role: int = 0
    other_times: int = 0


@dataclass(frozen=True)
class Snapshot:
    pets: tuple[Pet, ...]
    collected_config_ids: set[int]


@dataclass(frozen=True)
class BreedSnapshot:
    bases: tuple[Base, ...]
    uses: tuple[Use, ...]
    partners: tuple[Partner, ...]
    shelves: tuple[Shelf, ...]
    eggs: tuple[Egg, ...]


def _decode_text(value) -> str:
    if not isinstance(value, (bytes, bytearray)):
        return ""
    return bytes(value).decode("utf-8", errors="replace")


def _embedded(body: bytes, field_id: int) -> list[bytes]:
    return [
        bytes(value)
        for fid, value in codec.walk(body)
        if fid == field_id and isinstance(value, (bytes, bytearray))
    ]


def _varints(body: bytes, field_id: int) -> list[int]:
    return [
        int(value)
        for fid, value in codec.walk(body)
        if fid == field_id and isinstance(value, int)
    ]


def parse_key_values(values: Iterable[bytes]) -> dict[int, int]:
    out: dict[int, int] = {}
    for value in values:
        fields = codec.walk_dict(value)
        out[int(fields.get(1, 0))] = int(fields.get(2, 0))
    return out


def parse_pet(body: bytes) -> Pet:
    fields = codec.walk_dict(body)
    entries = tuple(
        Entry(
            id=int(codec.walk_dict(value).get(1, 0)),
            level=int(codec.walk_dict(value).get(2, 0)),
        )
        for value in _embedded(body, 15)
    )
    return Pet(
        id=int(fields.get(1, 0)),
        role_id=int(fields.get(2, 0)),
        config_id=int(fields.get(3, 0)),
        level=int(fields.get(4, 0)),
        step=int(fields.get(5, 0)),
        exp=int(fields.get(6, 0)),
        name=_decode_text(fields.get(7)),
        generation=int(fields.get(8, 0)),
        fight=int(fields.get(9, 0)),
        growth=int(fields.get(10, 0)),
        quality=int(fields.get(13, 0)),
        entries=entries,
        ext=parse_key_values(_embedded(body, 16)),
    )


def parse_pet_info(body: bytes) -> list[Pet]:
    return [parse_pet(value) for value in _embedded(body, 1)]


def parse_base_pet(body: bytes) -> Pet:
    """解析 ``type.p_fly_base_pet``（不是完整 p_fly_pet，欄位 4 是 name）。"""
    fields = codec.walk_dict(body)
    return Pet(
        id=int(fields.get(1, 0)),
        role_id=int(fields.get(2, 0)),
        config_id=int(fields.get(3, 0)),
        name=_decode_text(fields.get(4)),
        entries=tuple(
            Entry(
                id=int(codec.walk_dict(value).get(1, 0)),
                level=int(codec.walk_dict(value).get(2, 0)),
            )
            for value in _embedded(body, 5)
        ),
    )


def parse_egg(body: bytes) -> Egg:
    fields = codec.walk_dict(body)
    return Egg(
        id=int(fields.get(1, 0)),
        config_id=int(fields.get(2, 0)),
        ext=parse_key_values(_embedded(body, 3)),
    )


def parse_eggs(body: bytes, field_id: int = 1) -> list[Egg]:
    return [parse_egg(value) for value in _embedded(body, field_id)]


def parse_base(body: bytes) -> Base:
    fields = codec.walk_dict(body)
    fly_a_raw = fields.get(4)
    fly_b_raw = fields.get(5)
    return Base(
        id=int(fields.get(1, 0)),
        name=_decode_text(fields.get(2)),
        state=int(fields.get(3, 0)),
        fly_a=(parse_base_pet(bytes(fly_a_raw))
               if isinstance(fly_a_raw, (bytes, bytearray)) and fly_a_raw else None),
        fly_b=(parse_base_pet(bytes(fly_b_raw))
               if isinstance(fly_b_raw, (bytes, bytearray)) and fly_b_raw else None),
        start_time=int(fields.get(6, 0)),
        end_time=int(fields.get(7, 0)),
    )


def parse_use(body: bytes) -> Use:
    fields = codec.walk_dict(body)
    return Use(
        id=int(fields.get(1, 0)),
        times=int(fields.get(2, 0)),
        state=int(fields.get(3, 0)),
        start_time=int(fields.get(4, 0)),
        end_time=int(fields.get(5, 0)),
        other_times=int(fields.get(6, 0)),
        other_state=int(fields.get(7, 0)),
        other_start_time=int(fields.get(8, 0)),
        other_end_time=int(fields.get(9, 0)),
    )


def parse_partner(body: bytes) -> Partner:
    fields = codec.walk_dict(body)
    head_raw = fields.get(2)
    head = (
        int(codec.walk_dict(bytes(head_raw)).get(1, 0))
        if isinstance(head_raw, (bytes, bytearray)) else 0
    )
    return Partner(
        role_id=int(fields.get(1, 0)),
        name=_decode_text(fields.get(3)),
        head=head,
    )


def parse_shelf(body: bytes) -> Shelf | None:
    fields = codec.walk_dict(body)
    info = fields.get(1)
    if not isinstance(info, (bytes, bytearray)):
        return None
    return Shelf(
        info=parse_pet(bytes(info)),
        is_save=int(fields.get(2, 0)),
        state=int(fields.get(3, 0)),
        start_time=int(fields.get(4, 0)),
        end_time=int(fields.get(5, 0)),
        use_role=int(fields.get(6, 0)),
        other_times=int(fields.get(7, 0)),
    )


def _call(
    client,
    cmd: int,
    body: bytes = b"",
    *,
    timeout: float = 8.0,
    allow_error_codes: tuple[int, ...] = (),
) -> bytes:
    reply_cmd, reply = client.call_for(
        cmd, body, expect_cmds=(cmd, CMD_ERROR), timeout=timeout
    )
    if reply_cmd == CMD_ERROR:
        code = int(codec.walk_dict(reply).get(1, 0))
        if code in allow_error_codes:
            return b""
        raise FlyPetRPCError(code, cmd)
    return reply


def read_snapshot(client) -> Snapshot:
    # 遊戲原生 LOGIN_SUCCESS 後固定先送 66_1 再送 66_2。LIVE 5554 驗證：
    # 新連線第一發 66_1 在沒有蛋時可回 173，但它同時完成 fly 模組 bootstrap；
    # 若直接以 66_2 當第一發，66_2 也會被 173 擋下。故保留順序並只對 66_1
    # 容忍 173，後續真正資料 RPC 仍嚴格處理錯誤。
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    pets = tuple(parse_pet_info(_call(client, CMD_PET_INFO)))
    collected = set(_varints(_call(client, CMD_COLLECTION), 1))
    return Snapshot(pets=pets, collected_config_ids=collected)


def read_breed_snapshot(client) -> BreedSnapshot:
    egg_body = _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    base_body = _call(client, CMD_HYBRID_BASE_INFO)
    shelves_body = _call(client, CMD_HYBRID_SHELVES_INFO)
    bases = tuple(parse_base(value) for value in _embedded(base_body, 1))
    uses = tuple(parse_use(value) for value in _embedded(base_body, 2))
    partners = tuple(parse_partner(value) for value in _embedded(shelves_body, 2))
    shelves = tuple(
        shelf
        for value in _embedded(shelves_body, 3)
        if (shelf := parse_shelf(value)) is not None
    )
    return BreedSnapshot(
        bases=bases,
        uses=uses,
        partners=partners,
        shelves=shelves,
        eggs=tuple(parse_eggs(egg_body)),
    )


def read_partner_shelves(client, role_id: int) -> list[Shelf]:
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    body = codec.pb_uint(1, role_id)
    reply = _call(client, CMD_HYBRID_PARTNER_SHELVES, body)
    return [
        shelf
        for value in _embedded(reply, 1)
        if (shelf := parse_shelf(value)) is not None
    ]


def build_resolve_body(ids: Iterable[int]) -> bytes:
    return b"".join(codec.pb_uint(1, int(pet_id)) for pet_id in ids)


def select_safe_resolve_ids(
    pets: Iterable[Pet],
    requested_ids: Iterable[int],
    collected_config_ids: set[int],
) -> tuple[list[int], dict[str, int]]:
    by_id = {pet.id: pet for pet in pets}
    safe: list[int] = []
    skipped = {"locked": 0, "collected": 0, "deployed": 0, "missing": 0}
    for requested in requested_ids:
        pet = by_id.get(int(requested))
        if pet is None:
            skipped["missing"] += 1
        elif pet.lock:
            skipped["locked"] += 1
        elif pet.config_id in collected_config_ids:
            skipped["collected"] += 1
        elif pet.fight == 1:
            skipped["deployed"] += 1
        else:
            safe.append(pet.id)
    return safe, skipped


def resolve_pets(client, requested_ids: Iterable[int]) -> tuple[Snapshot, list[int], dict]:
    snapshot = read_snapshot(client)
    safe, skipped = select_safe_resolve_ids(
        snapshot.pets, requested_ids, snapshot.collected_config_ids
    )
    if not safe:
        return snapshot, safe, skipped
    reply = _call(client, CMD_PET_RESOLVE, build_resolve_body(safe))
    error_code = int(codec.walk_dict(reply).get(2, 0))
    if error_code:
        raise FlyPetRPCError(error_code, CMD_PET_RESOLVE)
    return snapshot, safe, skipped


def set_shelf(client, pet_id: int, action: str) -> Shelf | None:
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    type_value = 0 if action == "place" else 1
    body = (
        codec.pb_uint(1, pet_id)
        + codec.pb_uint(2, type_value)
        + codec.pb_uint(3, 0)
    )
    reply = _call(client, CMD_HYBRID_SET_SHELVES, body)
    fields = codec.walk_dict(reply)
    info = fields.get(2)
    return parse_shelf(bytes(info)) if isinstance(info, (bytes, bytearray)) else None


def start_breeding(client, base_id: int, fly_a_id: int, fly_b_id: int) -> Base:
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    body = (
        codec.pb_uint(1, base_id)
        + codec.pb_uint(2, fly_a_id)
        + codec.pb_uint(3, fly_b_id)
    )
    reply = _call(client, CMD_HYBRID_START, body)
    base = codec.walk_dict(reply).get(1)
    if not isinstance(base, (bytes, bytearray)):
        raise FlyPetRPCError(-1, CMD_HYBRID_START)
    return parse_base(bytes(base))


def collect_breeding(client, base_id: int) -> tuple[Base, list[Egg]]:
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    reply = _call(client, CMD_HYBRID_GET, codec.pb_uint(1, base_id))
    fields = codec.walk_dict(reply)
    base = fields.get(1)
    if not isinstance(base, (bytes, bytearray)):
        raise FlyPetRPCError(-1, CMD_HYBRID_GET)
    return parse_base(bytes(base)), parse_eggs(reply, field_id=2)


def hatch_egg(client, egg_id: int) -> list[int]:
    _call(client, CMD_EGG_INFO, allow_error_codes=(173,))
    reply = _call(client, CMD_EGG_INCUBATE, codec.pb_uint(1, egg_id))
    fields = codec.walk_dict(reply)
    error_code = int(fields.get(3, 0))
    if error_code:
        raise FlyPetRPCError(error_code, CMD_EGG_INCUBATE)
    return _varints(reply, 2)

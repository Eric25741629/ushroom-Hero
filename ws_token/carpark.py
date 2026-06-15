"""Cross (跨界) car-park parking over a logged-in WSGameClient — pure WS.

STRICT scope (user requirement): read a parking lot, find a cross-lot free slot,
park ONE of my mounts into it. Nothing else — no collecting, no warehouse, no
auto-collect toggle, no battle. Cross == ``type == 3``.

Schemas are the live-exported truth (docs/protocol/CARPARK_PROTO_SCHEMA.json +
TYPE_PROTO_SCHEMA.json, module 50; cmd = module*256 + N):

  car_park_info            12801  c2s {type#1:uint32, master_id#2:uint64, ceng#3:uint32}
                                  s2c {type#1, master_id#2, .., space_num#6,
                                       space_list#7:repeated p_car_park_space, .., ceng#12, ..}
  p_car_park_space  {pos#1, role_id#2:uint64, mount_id#3:uint32, mount_lev#4,
                     start_time#5, .., car_master_name#9:string}
        -> EMPTY slot iff role_id == 0 (corroborated by start_time==0 / blank name).
  car_park_car_info        12802  c2s {}  -> s2c {car_list#1:repeated p_car_park_car}
  p_car_park_car    {mount_id#1:uint32, car_lev#2, car_exp#3, minute#4,
                     parking_data#5:p_car_park_parking (present iff already parking)}

  cross_car_park_new_parking_start 12847  {park_id#1:uint64, pos#2:uint32, mount_id#3:uint64}
  cross_car_park_parking_start     12832  {id#1:uint64,      mount_id#2:uint64, pos#3:uint32}
        -> NEW has pos#2 / mount_id#3; OLD has mount_id#2 / pos#3 (swapped!).
  cross_car_park_new_parking_start_s2c {park_id#1, space#2:p_car_park_space}
  cross_car_park_parking_start_s2c     {id#1,      space#2:p_car_park_space}
  errors                           0x0201 error.error_info_s2c {error_code#1}

LIVE-CONFIRM (not yet verified against the server — see report):
  (1) target_id source: the NEW cross target id is not yet traced. The OLD path
      gets candidate park ids from cross_car_park_preview (12830) -> park_list;
      a single lot's detail comes from cross_car_park_info (12831) {id}. For NEW,
      pass the park_id in explicitly until the source is confirmed.
  (2) new vs old: the client flips on a runtime ``checkNewCrossOpen`` flag. We
      default new=True and let the caller fall back to new=False; reading the lot
      first (type==3 confirms cross) is the safe pre-check.
  (3) mount eligibility: we only exclude mounts already parking (parking_data
      present). Other gates (level/skin requirements) are not modelled yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from ws_token import codec
from ws_token import state as ws_state
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_LOT_INFO = 12801          # car_park.car_park_info (c2s == s2c id)
CMD_CAR_INFO = 12802          # car_park.car_park_car_info -> my mounts
CMD_SEARCH = 12808            # car_park.car_park_search {type,park_name} -> null_space
CMD_CROSS_PREVIEW = 12830     # car_park.cross_car_park_preview -> lot list (legacy)
CMD_CROSS_INFO = 12831        # car_park.cross_car_park_info {id} -> lot detail (legacy)

# car_park_search type enum (client ParkingListName): the NEW cross flow lists
# parkable lots via search type=4 (crossSpaceList) -> null_space, NOT preview.
SEARCH_TYPE_CROSS = 4
CMD_CROSS_OLD_START = 12832   # car_park.cross_car_park_parking_start (legacy)
CMD_CROSS_NEW_START = 12847   # car_park.cross_car_park_new_parking_start
CMD_COLLECT_BAG = 12846       # car_park.car_park_collect_all_bag_rewards (一鍵領倉庫
                              # 停車收益; UI ParkingWareHouseView 的「一鍵領取」,
                              # docs/protocol/CARPARK_GUILD_NODES.md)
CMD_ERROR = 0x0201            # error.error_info_s2c {error_code#1}

CROSS_TYPE = 3                # park type that marks a cross-server (跨界) lot
# A cross lot holds 10 slots, pos is 1-based (1..10). The s2c space_list for a
# cross lot lists ONLY occupied slots — empty positions are absent — so a free
# pos is derived as {1..capacity} minus the occupied set (LIVE-verified on 小寶:
# null_num == capacity - len(occupied); pos values seen up to 10, never 0).
CROSS_LOT_CAPACITY = 10       # config park_cross_each_num

# --- 泊銀 (silver tier of the cross park) ------------------------------------
# 泊銀 is NOT a separate park: it is pool id=3 of the 跨界 (cross) park. The
# same verified protocol applies (search 12808 type=4 lists ALL pools' lots;
# info 12801 type=3; park 12847). Pool membership is encoded in ``ceng``
# (== master_id % 1000): configCross_parking_lot dump 2026-05-20 gives silver
# internal lot ids 5..34 == user-visible 鉑銀1..30; LIVE re-confirmed 2026-06-13
# on 小寶 (search type=4 returned ceng 1..68 contiguous, master_id 1001001NNN).
SILVER_CENG_BASE = 5          # ceng of 鉑銀1
SILVER_LOT_COUNT = 30         # 鉑銀1 .. 鉑銀30
SILVER_PREFERRED_LEVELS = (9, 10)  # user policy 2026-06-13: 鉑銀9/10 first

# 搶位分層 (user 2026-06-15)：preferred(9/10) > 低編號抱團(1..LOW_MAX, 同服>=min)
# > 中編號(LOW_MAX+1..MID_MAX, 即 11..20) > 高編號(MID_MAX+1..30) > 低編號非抱團
# (最後手段)。preferred 不在低/中/高任何一層 (9/10 介於低與中之間)。
SILVER_LOW_MAX_LEVEL = 8      # 高獎勵低編號區 鉑銀1..8 (需同服抱團才停)
SILVER_MID_MAX_LEVEL = 20     # 中編號區 鉑銀11..20 (扣掉 preferred 9/10)
DEFAULT_CLUSTER_MIN = 3       # 同服占用 >= this 算抱團
DEFAULT_ALLOW_LOW_NONCLUSTER = True  # 最後手段：停低編號非抱團空位 (使用者拍板)


def silver_level_to_ceng(level: int) -> int:
    """User-visible 鉑銀 level (1..30) -> internal ceng (5..34)."""
    return SILVER_CENG_BASE + int(level) - 1


def silver_ceng_to_level(ceng: int) -> int:
    """Internal ceng (5..34) -> user-visible 鉑銀 level (1..30)."""
    return ceng - SILVER_CENG_BASE + 1


def is_silver_ceng(ceng: int) -> bool:
    return SILVER_CENG_BASE <= ceng < SILVER_CENG_BASE + SILVER_LOT_COUNT


@dataclass(frozen=True)
class Space:
    """One slot from car_park_info_s2c.space_list (p_car_park_space)."""

    pos: int            # #1
    role_id: int        # #2 (0 -> empty)
    occupied: bool      # derived: role_id != 0
    # role attr values from info_list#6 (p_role_change.kv) + ext#8 (p_key_value
    # list), flattened {k: v}. Used to spot same-server occupants (one of the
    # attrs carries the occupant's server id == 本服 master_id, e.g. 1467).
    attrs: dict = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class CarParkLot:
    """A parking lot snapshot (car_park_info_s2c)."""

    type: int                       # #1
    master_id: int                  # #2
    ceng: int                       # #12
    spaces: tuple[Space, ...]       # #7 space_list
    raw: dict = field(compare=False, default_factory=dict)

    @property
    def is_cross(self) -> bool:
        return self.type == CROSS_TYPE

    def free_positions(self) -> list[int]:
        return [s.pos for s in self.spaces if not s.occupied]

    def first_free_pos(self) -> int | None:
        free = self.free_positions()
        return free[0] if free else None

    def occupied_positions(self) -> set[int]:
        return {s.pos for s in self.spaces if s.occupied}

    def first_free_cross_pos(self, capacity: int = CROSS_LOT_CAPACITY) -> int | None:
        """First empty 1-based pos for a CROSS lot (space_list lists only the
        occupied slots, so derive free = {1..capacity} minus occupied)."""
        occ = self.occupied_positions()
        for p in range(1, capacity + 1):
            if p not in occ:
                return p
        return None


@dataclass(frozen=True)
class NullSpace:
    """One entry of car_park_search_s2c.null_space (p_car_park_null).

    The NEW cross flow's authoritative lot list. ``null_num`` is the count of
    EMPTY slots in that lot (a lot has 10 slots); ``master_id`` + ``ceng`` are
    what car_park_info(type=3) needs to read the per-slot detail, and what
    new_parking_start needs as ``park_id`` (= master_id).
    """

    park_type: int      # #1
    master_id: int      # #2 (== park_id for new_parking_start)
    null_num: int       # #3 (empty-slot count; >0 means parkable)
    ceng: int           # #7


@dataclass(frozen=True)
class CrossLotPreview:
    """One entry of cross_car_park_preview_s2c.park_list (p_cross_car_park_preview)."""

    id: int             # #1
    server_id: int      # #2
    protect_end: int    # #3 (epoch; lot under protection until then)
    def_num: int        # #4
    atk_num: int        # #5


@dataclass(frozen=True)
class CrossLotDetail:
    """cross_car_park_info_s2c.park_info (p_cross_car_park)."""

    id: int                         # #1
    server_id: int                  # #2
    spaces: tuple[Space, ...]       # #3 space_list
    raw: dict = field(compare=False, default_factory=dict)

    def free_positions(self) -> list[int]:
        return [s.pos for s in self.spaces if not s.occupied]

    def first_free_pos(self) -> int | None:
        free = self.free_positions()
        return free[0] if free else None


@dataclass(frozen=True)
class ParkingInfo:
    """p_car_park_parking (#5 of p_car_park_car) — where/when a mount is parked.

    Present-but-all-zero for an IDLE mount (the live server always sends the
    sub-message); a non-zero one means the mount is actually parked. ``type``
    mirrors the lot type (``CROSS_TYPE`` == 跨界); ``start_time`` is the parked-at
    epoch second (counts UP — elapsed = now - start_time; the game auto-collects
    the car at park_max_seconds, default 8h).
    """

    type: int           # #1 (3 == cross)
    master_id: int      # #2 (the lot)
    pos: int            # #3 (1-based slot)
    start_time: int     # #4 (epoch seconds the car was parked)


@dataclass(frozen=True)
class Mount:
    """One of my mounts (p_car_park_car)."""

    mount_id: int       # #1
    car_lev: int        # #2
    parking: bool       # derived: parking_data (#5) present
    parking_info: ParkingInfo | None = None  # populated only when actually parked


@dataclass(frozen=True)
class ParkResult:
    success: bool
    response_cmd: int
    response_body: bytes
    fields: dict
    park_id: int | None = None       # echoed lot id (#1 of start s2c)
    pos: int | None = None
    mount_id: int | None = None
    error_code: int | None = None
    error: str | None = None


# --- parsing ----------------------------------------------------------------

def _parse_kv_list(container: bytes, *, kv_field: int = 1) -> dict:
    """Flatten repeated p_key_value {k#1, v#2} sub-messages into {k: v}."""
    out: dict = {}
    for fn, v in codec.walk(container):
        if fn != kv_field or not isinstance(v, (bytes, bytearray)):
            continue
        kv = codec.walk_dict(bytes(v))
        k = kv.get(1)
        if isinstance(k, int):
            out[k] = kv.get(2)
    return out


def _parse_space(entry: bytes) -> Space:
    d = codec.walk_dict(entry)
    role_id = _as_int(d.get(2))
    attrs: dict = {}
    for fn, v in codec.walk(entry):
        if fn == 6 and isinstance(v, (bytes, bytearray)):   # info_list (p_role_change)
            attrs.update(_parse_kv_list(bytes(v)))
        elif fn == 8 and isinstance(v, (bytes, bytearray)):  # ext (p_key_value)
            kv = codec.walk_dict(bytes(v))
            k = kv.get(1)
            if isinstance(k, int):
                attrs[k] = kv.get(2)
    return Space(pos=_as_int(d.get(1)), role_id=role_id,
                 occupied=role_id != 0, attrs=attrs)


def count_same_server(lot: CarParkLot, server_id: int) -> int:
    """Count occupied slots whose role attrs carry ``server_id`` (本服抱團).

    p_car_park_space has no dedicated server field; the occupant's server id
    rides in the info_list/ext role attrs (value == 本服 master_id, e.g. 1467).
    Generic value-match keeps this schema-tolerant; a lot with no attr data
    counts 0, so callers degrade to plain occupancy ranking.
    """
    if not server_id:
        return 0
    n = 0
    for s in lot.spaces:
        if s.occupied and any(v == server_id for v in s.attrs.values()):
            n += 1
    return n


def parse_car_park_info(body: bytes) -> CarParkLot:
    """Decode car_park_info_s2c -> CarParkLot. is_cross = type==3; empty=role_id==0."""
    d = codec.walk_dict(body)
    spaces = tuple(
        _parse_space(bytes(v)) for fnum, v in codec.walk(body)
        if fnum == 7 and isinstance(v, (bytes, bytearray))
    )
    return CarParkLot(
        type=_as_int(d.get(1)),
        master_id=_as_int(d.get(2)),
        ceng=_as_int(d.get(12)),
        spaces=spaces,
        raw=d,
    )


def _is_parking(parking_data: object) -> bool:
    """A mount is actually parked only when its ``parking_data`` (#5) sub-message
    carries a NON-ZERO field.

    The live server sends an all-zero ``parking_data`` (every field 0, e.g.
    ``{1:0,2:0,3:0,4:0,5:0,6:0}``) for an IDLE mount — so the mere presence of the
    sub-message does NOT mean parked. The old ``d.get(5) is not None`` check
    excluded every free mount (verified on 小寶: 6 mounts -> 0 returned), which
    would make ``auto_park_cross`` always report ``no_available_mount``.
    """
    if not isinstance(parking_data, (bytes, bytearray)):
        return bool(parking_data)
    for _fn, v in codec.walk(bytes(parking_data)):
        if isinstance(v, int) and v != 0:
            return True
        if isinstance(v, (bytes, bytearray)) and _is_parking(v):
            return True
    return False


def _parse_parking_data(parking_data: object) -> ParkingInfo | None:
    """Decode p_car_park_parking (#5). Returns None when the sub-message is
    absent or all-zero (idle mount); else the parked location + start_time."""
    if not isinstance(parking_data, (bytes, bytearray)):
        return None
    d = codec.walk_dict(bytes(parking_data))
    info = ParkingInfo(
        type=_as_int(d.get(1)), master_id=_as_int(d.get(2)),
        pos=_as_int(d.get(3)), start_time=_as_int(d.get(4)),
    )
    if info == ParkingInfo(type=0, master_id=0, pos=0, start_time=0):
        return None
    return info


def _parse_car(entry: bytes) -> Mount:
    d = codec.walk_dict(entry)
    pdata = d.get(5)
    return Mount(
        mount_id=_as_int(d.get(1)),
        car_lev=_as_int(d.get(2)),
        parking=_is_parking(pdata),
        parking_info=_parse_parking_data(pdata),
    )


def parse_all_cars(body: bytes) -> list[Mount]:
    """Decode car_park_car_info_s2c.car_list — ALL mounts (idle + parked)."""
    return [
        _parse_car(bytes(v)) for fnum, v in codec.walk(body)
        if fnum == 1 and isinstance(v, (bytes, bytearray))
    ]


def parse_my_mounts(body: bytes) -> list[Mount]:
    """Decode car_park_car_info_s2c.car_list; excludes mounts already parking."""
    return [m for m in parse_all_cars(body) if not m.parking]


def parse_parked_cross(body: bytes) -> list[Mount]:
    """My cars currently parked in a CROSS lot (type==3), each with
    ``parking_info.start_time`` — the source for 8h auto-collect re-park timing."""
    return [m for m in parse_all_cars(body)
            if m.parking_info is not None and m.parking_info.type == CROSS_TYPE]


def parse_null_spaces(body: bytes) -> list[NullSpace]:
    """Decode car_park_search_s2c.null_space (#1) -> [NullSpace]."""
    out = []
    for fnum, v in codec.walk(body):
        if fnum != 1 or not isinstance(v, (bytes, bytearray)):
            continue
        d = codec.walk_dict(bytes(v))
        out.append(NullSpace(
            park_type=_as_int(d.get(1)), master_id=_as_int(d.get(2)),
            null_num=_as_int(d.get(3)), ceng=_as_int(d.get(7)),
        ))
    return out


def parse_collect_spaces(body: bytes) -> list[NullSpace]:
    """Decode car_park_search_s2c.collect_space (#2) -> [NullSpace].

    Bookmark/favorite spots from the same 12808 response. Same inner schema
    as null_space (p_car_park_null).
    """
    out = []
    for fnum, v in codec.walk(body):
        if fnum != 2 or not isinstance(v, (bytes, bytearray)):
            continue
        d = codec.walk_dict(bytes(v))
        out.append(NullSpace(
            park_type=_as_int(d.get(1)), master_id=_as_int(d.get(2)),
            null_num=_as_int(d.get(3)), ceng=_as_int(d.get(7)),
        ))
    return out


def parse_cross_preview(body: bytes) -> list[CrossLotPreview]:
    """Decode cross_car_park_preview_s2c.park_list -> [CrossLotPreview]."""
    lots = []
    for fnum, v in codec.walk(body):
        if fnum != 1 or not isinstance(v, (bytes, bytearray)):
            continue
        d = codec.walk_dict(bytes(v))
        lots.append(CrossLotPreview(
            id=_as_int(d.get(1)), server_id=_as_int(d.get(2)),
            protect_end=_as_int(d.get(3)), def_num=_as_int(d.get(4)),
            atk_num=_as_int(d.get(5)),
        ))
    return lots


def parse_cross_info(body: bytes) -> CrossLotDetail:
    """Decode cross_car_park_info_s2c.park_info -> CrossLotDetail."""
    park = b""
    for fnum, v in codec.walk(body):
        if fnum == 1 and isinstance(v, (bytes, bytearray)):
            park = bytes(v)
            break
    d = codec.walk_dict(park)
    spaces = tuple(
        _parse_space(bytes(v)) for fnum, v in codec.walk(park)
        if fnum == 3 and isinstance(v, (bytes, bytearray))
    )
    return CrossLotDetail(id=_as_int(d.get(1)), server_id=_as_int(d.get(2)),
                          spaces=spaces, raw=d)


# --- c2s body builders ------------------------------------------------------

def build_lot_info_body(*, type_: int, master_id: int, ceng: int = 0) -> bytes:
    """car_park_info_c2s {type#1, master_id#2, ceng#3}."""
    return (codec.pb_uint(1, type_) + codec.pb_uint(2, master_id)
            + codec.pb_uint(3, ceng))


def build_cross_new_start_body(*, park_id: int, pos: int, mount_id: int) -> bytes:
    """cross_car_park_new_parking_start_c2s {park_id#1, pos#2, mount_id#3}.

    NOTE: pos is #2 and mount_id is #3 — the OPPOSITE order from the legacy body.
    """
    return (codec.pb_uint(1, park_id) + codec.pb_uint(2, pos)
            + codec.pb_uint(3, mount_id))


def build_cross_old_start_body(*, id_: int, mount_id: int, pos: int) -> bytes:
    """cross_car_park_parking_start_c2s {id#1, mount_id#2, pos#3} (legacy).

    NOTE: mount_id is #2 and pos is #3 — swapped vs the NEW body. Do not mix.
    """
    return (codec.pb_uint(1, id_) + codec.pb_uint(2, mount_id)
            + codec.pb_uint(3, pos))


def build_cross_info_body(*, id_: int) -> bytes:
    """cross_car_park_info_c2s {id#1}."""
    return codec.pb_uint(1, id_)


def build_search_body(*, type_: int, park_name: str = "") -> bytes:
    """car_park_search_c2s {type#1, park_name#2}."""
    out = codec.pb_uint(1, type_)
    if park_name:
        out += codec.pb_str(2, park_name)
    return out


# --- reads ------------------------------------------------------------------

def read_cross_null_spaces(client: WSGameClient, *,
                           timeout: float | None = None) -> list[NullSpace]:
    """List parkable cross lots via car_park_search (12808, type=4).

    This is the NEW cross flow's authoritative source (the client fires it as
    ``reqParkSearch(crossSpaceList, "")`` when entering the cross view). Returns
    the null_space entries; filter on ``null_num > 0`` for lots with a free slot.
    """
    body = client.call(CMD_SEARCH,
                       build_search_body(type_=SEARCH_TYPE_CROSS),
                       timeout=timeout)
    return parse_null_spaces(body)


def read_cross_null_and_collect(client: WSGameClient, *,
                                timeout: float | None = None
                                ) -> tuple[list[NullSpace], list[NullSpace]]:
    """Single 12808 call returning (null_space, collect_space).

    ``collect_space`` (#2) is the user-bookmarked subset — the preferred
    candidate list for cross parking.  ``null_space`` (#1) is the full set.
    """
    body = client.call(CMD_SEARCH,
                       build_search_body(type_=SEARCH_TYPE_CROSS),
                       timeout=timeout)
    return parse_null_spaces(body), parse_collect_spaces(body)


def read_cross_preview(client: WSGameClient, *,
                       timeout: float | None = None) -> list[CrossLotPreview]:
    """Fetch the cross lot list via cross_car_park_preview (12830; empty c2s)."""
    return parse_cross_preview(client.call(CMD_CROSS_PREVIEW, b"", timeout=timeout))


def read_cross_info(client: WSGameClient, *, id_: int,
                    timeout: float | None = None) -> CrossLotDetail:
    """Fetch one cross lot's detail via cross_car_park_info (12831)."""
    return parse_cross_info(client.call(CMD_CROSS_INFO, build_cross_info_body(id_=id_),
                                        timeout=timeout))



def read_lot(client: WSGameClient, *, type: int = CROSS_TYPE, master_id: int,
             ceng: int = 0, timeout: float | None = None) -> CarParkLot:
    """Fetch a lot via car_park_info (12801). Defaults to the cross type."""
    body = client.call(CMD_LOT_INFO,
                       build_lot_info_body(type_=type, master_id=master_id, ceng=ceng),
                       timeout=timeout)
    return parse_car_park_info(body)


def read_my_mounts(client: WSGameClient, *,
                   timeout: float | None = None) -> list[Mount]:
    """Fetch my mount list via car_park_car_info (12802), excluding busy mounts."""
    return parse_my_mounts(client.call(CMD_CAR_INFO, b"", timeout=timeout))


def read_parked_cross(client: WSGameClient, *,
                      timeout: float | None = None) -> list[Mount]:
    """My cars currently parked in CROSS lots via car_park_car_info (12802).

    Each returned Mount carries ``parking_info.start_time`` (parked-at epoch) so
    the carpark plan can tell how many cross cars are live and when the earliest
    one hits the 8h auto-collect — the trigger to wake and re-park.
    """
    return parse_parked_cross(client.call(CMD_CAR_INFO, b"", timeout=timeout))


def collect_bag_rewards(client: WSGameClient, *,
                        timeout: float | None = None) -> dict:
    """一鍵領取停車倉庫收益 (car_park_collect_all_bag_rewards, 12846).

    Free claim of already-earned parking income (本服 + 跨界 cars both deposit
    here). Idempotent: with nothing to claim the server declines with 0x0201
    error_code=173 (LIVE 小寶 2026-06-13; the list cmd 12845
    car_park_bag_rewards answered empty at the same moment) — reported as
    success=False, never raised. Returns {success, response_cmd, fields,
    error_code}.
    """
    cmd, body = client.call_for(CMD_COLLECT_BAG, b"",
                                expect_cmds=(CMD_COLLECT_BAG, CMD_ERROR),
                                timeout=timeout)
    f = codec.walk_dict(body)
    if cmd == CMD_COLLECT_BAG:
        logger.info("ws_token carpark: warehouse collect ok fields=%s", f)
        return {"success": True, "response_cmd": cmd, "fields": f,
                "error_code": None}
    ec = f.get(1)
    ec = int(ec) if isinstance(ec, int) else None
    logger.info("ws_token carpark: warehouse collect declined code=%s", ec)
    return {"success": False, "response_cmd": cmd, "fields": f,
            "error_code": ec}


# --- today-dedup (bookmark filter) -------------------------------------------

def _load_today_parked_master_ids(device: str | None,
                                  state_dir=None) -> set[int]:
    """Load master_ids already parked today from ws_state for dedup."""
    if not device:
        return set()
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    st = ws_state.load_state(device, **kw)
    cp = st.get("carpark_parked_today") or {}
    if cp.get("date") != date.today().isoformat():
        return set()
    return set(cp.get("parked") or ())


def _record_park_today(device: str | None, master_id: int,
                       state_dir=None) -> None:
    """Append master_id to today's parked set in ws_state."""
    if not device:
        return
    today = date.today().isoformat()
    kw = {"state_dir": state_dir} if state_dir is not None else {}
    st = ws_state.load_state(device, **kw)
    cp = st.get("carpark_parked_today") or {}
    if cp.get("date") != today:
        cp = {"date": today, "parked": []}
    parked = list(cp.get("parked") or ())
    if master_id not in parked:
        parked.append(master_id)
    cp["parked"] = parked
    st["carpark_parked_today"] = cp
    ws_state.save_state(device, st, **kw)


# --- park -------------------------------------------------------------------

def _parse_start_result(cmd: int, body: bytes, *, expect_cmd: int,
                        pos: int, mount_id: int) -> ParkResult:
    f = codec.walk_dict(body)
    if cmd == expect_cmd:
        return ParkResult(True, cmd, body, f, park_id=_as_int(f.get(1)),
                          pos=pos, mount_id=mount_id)
    if cmd == CMD_ERROR:
        ec = f.get(1)
        ec = int(ec) if isinstance(ec, int) else None
        return ParkResult(False, cmd, body, f, pos=pos, mount_id=mount_id,
                          error_code=ec, error=f"server error code={ec}")
    return ParkResult(False, cmd, body, f, pos=pos, mount_id=mount_id,
                      error=f"unexpected response cmd 0x{cmd:04x}")


def park_into_cross(client: WSGameClient, *, target_id: int, pos: int,
                    mount_id: int, new: bool = True,
                    timeout: float | None = None) -> ParkResult:
    """Park ``mount_id`` into ``pos`` of cross lot ``target_id``.

    Reply is the matching start s2c on success, or 0x0201 on error. ``new``
    selects the 12847 body (park_id#1/pos#2/mount_id#3) vs the legacy 12832
    body (id#1/mount_id#2/pos#3).
    """
    if new:
        start_cmd = CMD_CROSS_NEW_START
        body = build_cross_new_start_body(park_id=target_id, pos=pos,
                                          mount_id=mount_id)
    else:
        start_cmd = CMD_CROSS_OLD_START
        body = build_cross_old_start_body(id_=target_id, mount_id=mount_id, pos=pos)
    cmd, reply = client.call_for(start_cmd, body,
                                 expect_cmds=(start_cmd, CMD_ERROR),
                                 timeout=timeout)
    return _parse_start_result(cmd, reply, expect_cmd=start_cmd,
                               pos=pos, mount_id=mount_id)


def auto_park_cross(client: WSGameClient, *, target_id: int, new: bool = True,
                    timeout: float | None = None) -> dict:
    """Read the cross lot, pick the first free slot + an available mount, park.

    Returns a dict describing the outcome:
      {parked, reason, pos, mount_id, lot, result}
    Never collects, never battles, never touches non-cross lots.
    """
    lot = read_lot(client, type=CROSS_TYPE, master_id=target_id, ceng=0,
                   timeout=timeout)
    pos = lot.first_free_pos()
    if pos is None:
        logger.info("ws_token carpark: lot %s has no free slot", target_id)
        return {"parked": False, "reason": "no_free_slot", "pos": None,
                "mount_id": None, "lot": lot, "result": None}

    mounts = read_my_mounts(client, timeout=timeout)
    if not mounts:
        logger.info("ws_token carpark: no available (non-parking) mount")
        return {"parked": False, "reason": "no_available_mount", "pos": pos,
                "mount_id": None, "lot": lot, "result": None}

    mount_id = mounts[0].mount_id
    result = park_into_cross(client, target_id=target_id, pos=pos,
                             mount_id=mount_id, new=new, timeout=timeout)
    if not result.success:
        logger.warning("ws_token carpark: park failed target=%s pos=%s mount=%s "
                       "code=%s", target_id, pos, mount_id, result.error_code)
        return {"parked": False, "reason": "park_failed", "pos": pos,
                "mount_id": mount_id, "lot": lot, "result": result}

    logger.info("ws_token carpark: parked mount=%s into cross %s pos=%s",
                mount_id, target_id, pos)
    return {"parked": True, "reason": "ok", "pos": pos, "mount_id": mount_id,
            "lot": lot, "result": result}


def auto_select_and_park(client: WSGameClient, *, new: bool = True,
                         device: str | None = None, state_dir=None,
                         timeout: float | None = None) -> dict:
    """Fully automatic cross parking: search -> per-lot detail -> park.

    Reads my mounts first (cheap short-circuit), then lists parkable cross lots
    via car_park_search (type=4 -> collect_space; the user-bookmarked subset).
    For each lot with ``null_num > 0`` it reads the slot detail to pick the
    exact free pos, then attempts ONE park. A rejected park (race / protected)
    falls through to the next lot. Strictly park-only, cross-only.

    Skips lots already parked today (dedup by master_id in ws_state).

    ``park_id`` for new_parking_start is the lot's ``master_id`` (verified from
    the client: btn click -> reqParkingInfo(3, master_id, ceng), then
    reqNewCrossParkingStart(park_id=master_id, ...)).

    Returns {parked, reason, target_id, pos, mount_id, attempts, lots, result}.
    """
    mounts = read_my_mounts(client, timeout=timeout)
    if not mounts:
        logger.info("ws_token carpark: no available (non-parking) mount")
        return {"parked": False, "reason": "no_available_mount", "target_id": None,
                "pos": None, "mount_id": None, "attempts": 0, "lots": 0,
                "result": None}
    mount_id = mounts[0].mount_id

    _null, collect = read_cross_null_and_collect(client, timeout=timeout)
    if not collect:
        logger.info("ws_token carpark: collect_space empty (no bookmarked lots); "
                    "skip (not falling back to null_space)")
        return {"parked": False, "reason": "no_bookmarked_lot", "target_id": None,
                "pos": None, "mount_id": mount_id, "attempts": 0, "lots": 0,
                "result": None}

    today_parked = _load_today_parked_master_ids(device, state_dir)
    parkable = [lot for lot in collect
                if lot.null_num > 0 and lot.master_id not in today_parked]
    if not parkable:
        logger.info("ws_token carpark: %d bookmarked lot(s), 0 parkable "
                    "(after dedup %d already parked today)", len(collect),
                    len(today_parked))
        return {"parked": False, "reason": "no_cross_lot", "target_id": None,
                "pos": None, "mount_id": mount_id, "attempts": 0,
                "lots": len(collect), "result": None}

    attempts = 0
    last_result: ParkResult | None = None
    for lot in parkable:
        detail = read_lot(client, type=CROSS_TYPE, master_id=lot.master_id,
                          ceng=lot.ceng, timeout=timeout)
        pos = detail.first_free_cross_pos()
        if pos is None:
            continue
        attempts += 1
        result = park_into_cross(client, target_id=lot.master_id, pos=pos,
                                 mount_id=mount_id, new=new, timeout=timeout)
        last_result = result
        if result.success:
            _record_park_today(device, lot.master_id, state_dir)
            logger.info("ws_token carpark: parked mount=%s into cross %s pos=%s "
                        "(auto-selected, attempt %s)", mount_id, lot.master_id,
                        pos, attempts)
            return {"parked": True, "reason": "ok", "target_id": lot.master_id,
                    "pos": pos, "mount_id": mount_id, "attempts": attempts,
                    "lots": len(parkable), "result": result}
        logger.warning("ws_token carpark: park rejected lot=%s pos=%s code=%s; "
                       "trying next lot", lot.master_id, pos, result.error_code)

    reason = "park_failed" if attempts else "no_free_slot"
    logger.info("ws_token carpark: auto-select done parked=False reason=%s "
                "lots=%s attempts=%s", reason, len(parkable), attempts)
    return {"parked": False, "reason": reason, "target_id": None, "pos": None,
            "mount_id": mount_id, "attempts": attempts, "lots": len(parkable),
            "result": last_result}


def _silver_level(ceng: int) -> int:
    return silver_ceng_to_level(ceng)


def tiered_lot_order(
    parkable: list[NullSpace], *,
    preferred_cengs: set[int],
    cluster_min: int,
    same_counts: dict[int, int],
    allow_low_noncluster: bool = DEFAULT_ALLOW_LOW_NONCLUSTER,
) -> list[tuple[NullSpace, str]]:
    """Order silver cross lots by grab priority, each tagged with its tier.

    Tiers (high to low, user 2026-06-15):
      preferred (鉑銀9/10) -> low_cluster (鉑銀1..8 with 同服 occupants
      >= ``cluster_min``) -> mid (鉑銀11..20) -> high (鉑銀21..30) ->
      low_noncluster (鉑銀1..8 below the 抱團 gate; absolute last resort, kept
      only when ``allow_low_noncluster``).

    ``same_counts`` maps ``master_id`` -> that lot's 同服 occupant count (0 /
    absent for lots not probed). Within every tier lots are ordered by ceng
    ascending (編號小->大). Pure — no I/O — so the ranking is unit-testable.
    """
    preferred: list[NullSpace] = []
    low_cluster: list[NullSpace] = []
    mid: list[NullSpace] = []
    high: list[NullSpace] = []
    low_noncluster: list[NullSpace] = []
    for lot in parkable:
        if lot.ceng in preferred_cengs:
            preferred.append(lot)
            continue
        lv = _silver_level(lot.ceng)
        if lv <= SILVER_LOW_MAX_LEVEL:
            if same_counts.get(lot.master_id, 0) >= cluster_min:
                low_cluster.append(lot)
            else:
                low_noncluster.append(lot)
        elif lv <= SILVER_MID_MAX_LEVEL:
            mid.append(lot)
        else:
            high.append(lot)

    tiers: list[tuple[list[NullSpace], str]] = [
        (preferred, "preferred"), (low_cluster, "low_cluster"),
        (mid, "mid"), (high, "high")]
    if allow_low_noncluster:
        tiers.append((low_noncluster, "low_noncluster"))

    out: list[tuple[NullSpace, str]] = []
    for lots_in_tier, name in tiers:
        for lot in sorted(lots_in_tier, key=lambda l: l.ceng):
            out.append((lot, name))
    return out


def auto_select_and_park_many(client: WSGameClient, *, count: int = 1,
                              silver_only: bool = True,
                              prefer_levels: tuple[int, ...] | list[int]
                              = SILVER_PREFERRED_LEVELS,
                              cluster_server_id: int | None = None,
                              cluster_min: int = DEFAULT_CLUSTER_MIN,
                              allow_low_noncluster: bool
                              = DEFAULT_ALLOW_LOW_NONCLUSTER,
                              new: bool = True,
                              device: str | None = None,
                              state_dir=None,
                              timeout: float | None = None) -> dict:
    """Park up to ``count`` of my idle mounts into 跨界 silver lots, tiered.

    Multi-mount core for the day/night carpark plan (ws_token/carpark_plan.py):
    reads my mounts ONCE (read_my_mounts already excludes mounts that are
    parking), lists the cross lots via car_park_search (type=4 -> collect_space,
    the user-bookmarked subset), keeps only silver-tier lots (``silver_only``;
    跨界限定泊銀). Skips lots already parked today (dedup by master_id).

    Selection is tiered (user 2026-06-15): 鉑銀``prefer_levels`` (9/10) first and
    unconditionally — a free preferred lot is parked with the FEWEST round-trips
    (no 抱團 probe). Only when preferred can't fill ``count`` does it pay to read
    the 低編號 (鉑銀1..8) lots' details to count 同服 occupants and apply the
    抱團 gate (``cluster_min``), then fall through 中編號(11..20) ->
    高編號(21..30) -> (last resort) 低編號非抱團 (``allow_low_noncluster``). See
    ``tiered_lot_order``. A rejected park abandons that lot and falls through to
    the next. Strictly park-only.

    Returns {parked_count, requested, reason, results: [per-park dict]}.
    """
    requested = max(0, int(count))
    out: dict = {"parked_count": 0, "requested": requested, "reason": "ok",
                 "results": []}
    if requested == 0:
        out["reason"] = "zero_quota"
        return out

    mounts = read_my_mounts(client, timeout=timeout)
    if not mounts:
        logger.info("ws_token carpark: no available (non-parking) mount")
        out["reason"] = "no_available_mount"
        return out
    mount_queue = [m.mount_id for m in mounts]

    _null, collect = read_cross_null_and_collect(client, timeout=timeout)
    if not collect:
        logger.info("ws_token carpark: collect_space empty (no bookmarked lots); "
                    "skip (not falling back to null_space)")
        out["reason"] = "no_bookmarked_lot"
        return out

    today_parked = _load_today_parked_master_ids(device, state_dir)
    parkable = [lot for lot in collect
                if lot.null_num > 0
                and (not silver_only or is_silver_ceng(lot.ceng))
                and lot.master_id not in today_parked]
    if not parkable:
        logger.info("ws_token carpark: %d bookmarked lot(s), 0 parkable "
                    "(silver_only=%s, dedup %d today)", len(collect),
                    silver_only, len(today_parked))
        out["reason"] = "no_parkable_lot"
        return out

    preferred_cengs = {silver_level_to_ceng(lv) for lv in (prefer_levels or ())}
    details: dict[int, CarParkLot] = {}

    def _read_detail(lot: NullSpace) -> CarParkLot:
        d = details.get(lot.master_id)
        if d is None:
            d = read_lot(client, type=CROSS_TYPE, master_id=lot.master_id,
                         ceng=lot.ceng, timeout=timeout)
            details[lot.master_id] = d
        return d

    def _attempt(lots_in_order: list[NullSpace]) -> bool:
        """Park queued mounts into ``lots_in_order``. Returns True if a park
        timed out (caller must stop and return the confirmed partial)."""
        for lot in lots_in_order:
            if out["parked_count"] >= requested or not mount_queue:
                break
            try:
                detail = _read_detail(lot)
            except Exception:  # noqa: BLE001 — 讀單一 lot 失敗(非 mutating)，跳過該 lot；不可中斷已停車持久化
                logger.warning("ws_token carpark: read_lot %s failed; skip lot",
                               lot.master_id, exc_info=True)
                continue
            occupied = detail.occupied_positions()
            for pos in range(1, CROSS_LOT_CAPACITY + 1):
                if pos in occupied:
                    continue
                if out["parked_count"] >= requested or not mount_queue:
                    break
                mount_id = mount_queue[0]
                try:
                    result = park_into_cross(client, target_id=lot.master_id,
                                             pos=pos, mount_id=mount_id,
                                             new=new, timeout=timeout)
                except Exception:  # noqa: BLE001 — park 逾時可能已落地 server 端；停手回傳已確認 parked_count，讓 runner 持久化
                    logger.warning("ws_token carpark: park_into_cross timed out "
                                   "lot=%s pos=%s mount=%s; stopping, partial",
                                   lot.master_id, pos, mount_id, exc_info=True)
                    out["reason"] = "park_timeout"
                    return True
                out["results"].append({
                    "target_id": lot.master_id, "pos": pos,
                    "mount_id": mount_id, "success": result.success,
                    "error_code": result.error_code})
                if result.success:
                    _record_park_today(device, lot.master_id, state_dir)
                    mount_queue.pop(0)
                    out["parked_count"] += 1
                    logger.info("ws_token carpark: parked mount=%s lot=%s "
                                "pos=%s ceng=%s (%d/%d)", mount_id,
                                lot.master_id, pos, lot.ceng,
                                out["parked_count"], requested)
                else:
                    logger.warning("ws_token carpark: park rejected lot=%s "
                                   "pos=%s code=%s; next lot", lot.master_id,
                                   pos, result.error_code)
                    break  # abandon this lot, fall through to the next
        return False

    # Phase A — preferred (鉑銀9/10) 快路徑：有空位最少 RTT 直接停，不付抱團預讀。
    preferred_lots = sorted((lot for lot in parkable
                             if lot.ceng in preferred_cengs),
                            key=lambda l: l.ceng)
    if _attempt(preferred_lots):
        return out

    # Phase B — preferred 滿/搶輸才評估 fallback：預讀低編號 lot 算同服抱團，
    # 再分層走訪 (低區抱團 > 中 > 高 > 低區非抱團)。
    if out["parked_count"] < requested and mount_queue:
        same_counts: dict[int, int] = {}
        nonpref = [lot for lot in parkable if lot.ceng not in preferred_cengs]
        if cluster_server_id:
            for lot in nonpref:
                if _silver_level(lot.ceng) > SILVER_LOW_MAX_LEVEL:
                    continue
                try:
                    same_counts[lot.master_id] = count_same_server(
                        _read_detail(lot), cluster_server_id)
                except Exception:  # noqa: BLE001 — 一個 lot 讀失敗不擋整體
                    logger.debug("ws_token carpark: probe read_lot %s failed",
                                 lot.master_id, exc_info=True)
            if same_counts:
                logger.info("ws_token carpark: 低編號抱團 probe (server=%s): %s",
                            cluster_server_id,
                            {silver_ceng_to_level(lot.ceng):
                             same_counts.get(lot.master_id, 0)
                             for lot in nonpref
                             if lot.master_id in same_counts})
        order = tiered_lot_order(
            nonpref, preferred_cengs=preferred_cengs, cluster_min=cluster_min,
            same_counts=same_counts, allow_low_noncluster=allow_low_noncluster)
        if _attempt([lot for lot, _tier in order]):
            return out

    if out["parked_count"] < requested:
        out["reason"] = ("no_more_mounts" if not mount_queue
                         else "quota_unfilled")
    return out


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

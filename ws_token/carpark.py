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

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_LOT_INFO = 12801          # car_park.car_park_info (c2s == s2c id)
CMD_CAR_INFO = 12802          # car_park.car_park_car_info -> my mounts
CMD_CROSS_OLD_START = 12832   # car_park.cross_car_park_parking_start (legacy)
CMD_CROSS_NEW_START = 12847   # car_park.cross_car_park_new_parking_start
CMD_ERROR = 0x0201            # error.error_info_s2c {error_code#1}

CROSS_TYPE = 3                # park type that marks a cross-server (跨界) lot


@dataclass(frozen=True)
class Space:
    """One slot from car_park_info_s2c.space_list (p_car_park_space)."""

    pos: int            # #1
    role_id: int        # #2 (0 -> empty)
    occupied: bool      # derived: role_id != 0


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


@dataclass(frozen=True)
class Mount:
    """One of my mounts (p_car_park_car)."""

    mount_id: int       # #1
    car_lev: int        # #2
    parking: bool       # derived: parking_data (#5) present


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

def _parse_space(entry: bytes) -> Space:
    d = codec.walk_dict(entry)
    role_id = _as_int(d.get(2))
    return Space(pos=_as_int(d.get(1)), role_id=role_id, occupied=role_id != 0)


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


def _parse_car(entry: bytes) -> Mount:
    d = codec.walk_dict(entry)
    return Mount(
        mount_id=_as_int(d.get(1)),
        car_lev=_as_int(d.get(2)),
        parking=_is_parking(d.get(5)),
    )


def parse_my_mounts(body: bytes) -> list[Mount]:
    """Decode car_park_car_info_s2c.car_list; excludes mounts already parking."""
    mounts = [
        _parse_car(bytes(v)) for fnum, v in codec.walk(body)
        if fnum == 1 and isinstance(v, (bytes, bytearray))
    ]
    return [m for m in mounts if not m.parking]


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


# --- reads ------------------------------------------------------------------

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


def _as_int(v) -> int:
    return int(v) if isinstance(v, int) else 0

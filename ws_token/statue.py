"""菇菇雕像 (mushroom statue) — pure-WS fruit consumption.

Home module 12 (same as farm).  NOT yet live-verified; protocol confirmed from
game client JS (mushroomh5.acenetgame.com_assets_script_index.966f5.js) and
HOME_PROTO_SCHEMA.json.

Cmd map (cmd = module*256 + sub)::

  home_farm_statue_info   3089 (0x0C11)  c2s {}
                                          s2c {level#1:u32, exp#2:u32,
                                               tab#3:u32, show_tab#4:u32,
                                               tab_list#5:repeated}
  home_farm_spend_fruit   3107 (0x0C23)  c2s {spend_num#1:u32}
                                          s2c {result#1:u32}  (result=0 OK)

Errors land on CMD_ERROR = 0x0201 — see client.call_for().

Usage::

  r = statue.spend_fruit(client, 7000)
  if r['ok']:
      print('consumed', r['amount'], 'fruits')
"""
from __future__ import annotations

import logging
from typing import Optional

from ws_token import codec
from ws_token.client import WSGameClient

logger = logging.getLogger(__name__)

CMD_STATUE_INFO = 3089   # home_farm_statue_info (home module 12, sub 17)
CMD_SPEND_FRUIT = 3107   # home_farm_spend_fruit (home module 12, sub 35)
CMD_ERROR = 0x0201       # error.error_info_s2c {error_code#1}


def build_spend_body(amount: int) -> bytes:
    """home_farm_spend_fruit_c2s {spend_num#1: uint32}."""
    return codec.pb_uint(1, amount)


def read_statue_info(
    client: WSGameClient, *, timeout: Optional[float] = None
) -> dict:
    """home_farm_statue_info: read statue level/exp (empty c2s body).

    Returns {level, exp}.
    """
    body = client.call(CMD_STATUE_INFO, b"", timeout=timeout)
    d = codec.walk_dict(body)
    return {"level": int(d.get(1) or 0), "exp": int(d.get(2) or 0)}


def spend_fruit(
    client: WSGameClient, amount: int, *, timeout: Optional[float] = None
) -> dict:
    """home_farm_spend_fruit: consume ``amount`` fruit/vegetable tributes.

    Sends {spend_num#1=amount}, waits for a success reply (3107) OR an error
    reply (0x0201).  Returns::

      {ok: bool, result: int, amount: int}              # on success
      {ok: bool, result: int, amount: int,
       error_code: int}                                  # on rejection

    ``ok=True`` iff the server replied on 3107 with result#1==0.  Non-zero
    result or a 0x0201 rejection both yield ``ok=False``.
    """
    if amount <= 0:
        logger.info("ws_token statue: spend_fruit skipped (amount=%s)", amount)
        return {"ok": False, "result": -1, "amount": amount, "skipped": True}

    reply_cmd, reply = client.call_for(
        CMD_SPEND_FRUIT,
        build_spend_body(amount),
        expect_cmds=(CMD_SPEND_FRUIT, CMD_ERROR),
        timeout=timeout,
    )
    if reply_cmd == CMD_ERROR:
        code = int(codec.walk_dict(reply).get(1) or 0)
        logger.warning(
            "ws_token statue: spend_fruit rejected 0x0201 code=%s amount=%s",
            code, amount,
        )
        return {"ok": False, "result": code, "amount": amount, "error_code": code}

    result = int(codec.walk_dict(reply).get(1) or 0)
    ok = result == 0
    logger.info(
        "ws_token statue: spend_fruit amount=%s result=%s ok=%s",
        amount, result, ok,
    )
    return {"ok": ok, "result": result, "amount": amount}

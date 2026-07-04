"""菇菇武道會 競猜商店 (KungfuRaceStoreView) 競猜幣採購 over pure WS.

The 競猜商店 is just a Mall tab: shop module 27 with ``shop_type == 14``. Buying
競猜幣 (item 1161) with 粉鑽 (currency 2) goes through the generic shop_buy — no
kungfu-specific cmd. Verified live (5554 H5, 2026-06-14) by replaying the exact
frame the client sends:

    shop.shop_buy_c2s {shop_type#1:14, shop_id#2, num#3:1}  cmd 6914 (0x1B02)
      -> shop_buy_s2c {shop_id#1, num#2}                    cmd 6914 (success)
      -> error.error_info_s2c {error_code#1}                cmd 0x0201 (rejected:
         per-period limit reached / 粉鑽 不足 / event window closed)

The four 競猜幣 tiers (from client config ``configMall`` rows, shop_type 14):

    | shop_id | 競猜幣 | 花費        | 週期  | 限購 |
    |---------|--------|-------------|-------|------|
    | 15001   | 100    | 免費        | daily | 1    |
    | 15002   | 200    | 粉鑽 600    | daily | 1    |
    | 15003   | 300    | 粉鑽 1500   | weekly| 2    |
    | 15004   | 500    | 粉鑽 3000   | weekly| 3    |

(rows 15005+ buy 神燈/鑽石金鑰 with 競猜幣 currency 1162 — out of scope here.)

Design: blind-buy ``num=1`` up to each tier's per-period cap; stop a tier on the
first 0x0201. That makes the call idempotent (an already-maxed or out-of-window
tier rejects on the first attempt and we move on) WITHOUT depending on parsing
shop_info. Every tier — free and 粉鑽 alike — is bought to its cap unconditionally
(user 2026-06-14: 「買就對了」); clearing them all costs up to 12,600 粉鑽/week.

Reuse: ``ws_token.codec`` (pb_uint), ``ws_token.client.WSGameClient.call_for``.
Mirrors ``ws_token.farm`` / ``ws_token.steward`` shop usage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ws_token import codec
from ws_token.client import WSError, WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

SHOP_TYPE = 14                 # 競猜商店 (Mall tab discriminator)
ITEM_GUESS_COIN = 1161         # 競猜幣 (what these tiers grant)
CURRENCY_PINK_DIAMOND = 2      # 粉鑽 (what the paid tiers cost)

CMD_SHOP_BUY = 6914            # 0x1B02 shop.shop_buy_c2s/_s2c
CMD_ERR = 0x0201              # error.error_info_s2c (rejection channel)


@dataclass(frozen=True)
class GuessTier:
    """One 競猜幣 row in the 競猜商店."""

    shop_id: int
    coin_qty: int          # 競猜幣 granted per purchase
    diamond_price: int     # 0 == free
    limit: int             # per-period purchase cap
    period: str            # "daily" | "weekly" (for logging only; server enforces)


# Authoritative from configMall (shop_type 14), live-confirmed 2026-06-14.
GUESS_TIERS: tuple[GuessTier, ...] = (
    GuessTier(15001, 100, 0, 1, "daily"),
    GuessTier(15002, 200, 600, 1, "daily"),
    GuessTier(15003, 300, 1500, 2, "weekly"),
    GuessTier(15004, 500, 3000, 3, "weekly"),
)


@dataclass(frozen=True)
class GuessBuyReport:
    """Outcome of one ``claim_guess_coins`` pass.

    ``bought`` maps shop_id -> number of successful purchases this pass.
    ``coins`` is the total 競猜幣 granted. ``stopped`` maps shop_id -> the reason a
    tier stopped early (error code) when it did not reach its cap.
    """

    bought: dict[int, int] = field(default_factory=dict)
    coins: int = 0
    diamonds_spent: int = 0
    stopped: dict[int, int] = field(default_factory=dict)

    @property
    def any_bought(self) -> bool:
        return any(self.bought.values())


def build_shop_buy_body(shop_type: int, shop_id: int, num: int) -> bytes:
    """shop_buy_c2s {shop_type#1, shop_id#2, num#3}."""
    return (codec.pb_uint(1, shop_type)
            + codec.pb_uint(2, shop_id)
            + codec.pb_uint(3, num))


def _error_code(body: bytes) -> int:
    """error.error_info_s2c {error_code#1}; 0 if unparseable."""
    try:
        return int(codec.walk_dict(body).get(1) or 0)
    except Exception:
        return 0


def _buy_tier(client: WSGameClient, tier: GuessTier, *, timeout: float = 6.0) -> tuple[int, Optional[int]]:
    """Blind-buy ``tier`` up to its cap. Returns (success_count, stop_code).

    stop_code is None if the cap was reached cleanly, else the 0x0201 error code
    (or 0 for a timeout/unknown) that halted the tier early.
    """
    count = 0
    for _ in range(tier.limit):
        body = build_shop_buy_body(SHOP_TYPE, tier.shop_id, 1)
        try:
            rc, rb = client.call_for(
                CMD_SHOP_BUY, body,
                expect_cmds=(CMD_SHOP_BUY, CMD_ERR), timeout=timeout)
        except WSTimeoutError:
            logger.warning("kungfu_store: shop_id=%d timed out after %d buy(s)",
                           tier.shop_id, count)
            return count, 0
        except WSError as exc:
            logger.warning("kungfu_store: shop_id=%d WSError: %s", tier.shop_id, exc)
            return count, 0
        if rc == CMD_SHOP_BUY:
            count += 1
            continue
        code = _error_code(rb)
        logger.info("kungfu_store: shop_id=%d stop after %d buy(s), code=%s",
                    tier.shop_id, count, code)
        return count, code
    return count, None


def claim_guess_coins(client: WSGameClient, *, timeout: float = 6.0) -> GuessBuyReport:
    """Buy every 競猜幣 tier in the 競猜商店 to its per-period cap over pure WS.

    All four tiers (free + the three 粉鑽 tiers) are bought unconditionally — the
    server is the only gate. Idempotent: an already-maxed tier or a closed event
    window rejects on the first attempt (0x0201) and is skipped. Safe to call
    every run.
    """
    bought: dict[int, int] = {}
    stopped: dict[int, int] = {}
    coins = 0
    diamonds = 0
    for tier in GUESS_TIERS:
        n, stop_code = _buy_tier(client, tier, timeout=timeout)
        if n:
            bought[tier.shop_id] = n
            coins += n * tier.coin_qty
            diamonds += n * tier.diamond_price
        if stop_code is not None and n < tier.limit:
            stopped[tier.shop_id] = stop_code
    report = GuessBuyReport(bought=bought, coins=coins,
                            diamonds_spent=diamonds, stopped=stopped)
    if report.any_bought:
        logger.info("kungfu_store: +%d 競猜幣 (粉鑽 -%d) bought=%s",
                    coins, diamonds, bought)
    return report


if __name__ == "__main__":
    import sys
    sys.dont_write_bytecode = True
    import os
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from ws_token.creds import load_creds

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dev = sys.argv[1] if len(sys.argv) > 1 else "emulator-5554"
    c = WSGameClient(load_creds(dev))
    print("LOGIN", c.connect())
    try:
        rep = claim_guess_coins(c)
        print("REPORT", rep)
    finally:
        c.close()

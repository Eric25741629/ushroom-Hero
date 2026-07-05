"""限時商店 (ShopMainView PayLimitView) 禮包購買 — pure WS, module 19 (0x13xx).

Distinct from the generic ``shop.shop_buy_c2s`` (module 27, cmd 6914) used by
farm/carpark/kungfu store — this is the 商城/PayMall family that also owns
召喚 (PayGemView) / PayGiftView / PayLimitView tabs (see
docs/protocol/HOME_FEATURES_MUTATE_VERIFY_2026-06-10.md line 42). Config table
is ``configPay_mall`` (client global), NOT ``configMall``.

LIVE-VERIFIED (7fe98fc6, 2026-07-06) by hooking the real session's
netManager.sendMessage/reciveMsg (CDP, no new WS login) and emitting a real
click on the 每日商店/免費禮包 button:

    tx cmd 4866 (0x1302) = {1: bundle_id}                       body: 08 <varint bundle_id>
    rx cmd 1030 (0x0406) item_change push = {gtid, {item_id, num}}   -> reward granted
    rx cmd 4868 (0x1304) p_pay_mall_info push = {bundle_id, type, bought_times, end_time, is_reward}
    rx cmd 4866 (0x1302) ack, same body as tx (echo)

configPay_mall row for bundle_id 20101 (免費禮包, 每日商店 tab):
    [20101, 101195, 4, 1, 0, 0, 0, 0, '', '', '', [[2, 150]], 1, 1, [1, 16], ...]
    index 11 = reward list [[item_id, num], ...] = [[2, 150]] (鑽石 x150)
No price fields are sent client->server; the free/paid distinction lives
entirely in server-side config, so ``build_pay_mall_buy_body`` only needs
``bundle_id``.

Reject channel presumed ``error.error_info_s2c`` (cmd 0x0201), same as every
other shop family in this game — not yet observed live (the first live click
succeeded), so treat any non-4866/1030 reply defensively.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ws_token import codec
from ws_token.client import WSError, WSGameClient, WSTimeoutError

logger = logging.getLogger(__name__)

CMD_PAY_MALL_BUY = 4866         # 0x1302 pay_mall.pay_mall_buy_c2s / _s2c (echo ack)
CMD_ITEM_CHANGE = 0x0406        # 1030 item_change push (carries the actual reward)
CMD_PAY_MALL_INFO_PUSH = 4868   # 0x1304 p_pay_mall_info push
CMD_ERR = 0x0201                # error.error_info_s2c

# 每日商店 -> 免費禮包 (150 鑽石, daily, 限時商店 tab). bundle_id from configPay_mall.
FREE_GIFT_BUNDLE_ID = 20101
FREE_GIFT_REWARD_ITEM = 2       # 鑽石
FREE_GIFT_REWARD_QTY = 150


def build_pay_mall_buy_body(bundle_id: int) -> bytes:
    """pay_mall_buy_c2s {bundle_id#1}."""
    return codec.pb_uint(1, bundle_id)


def _error_code(body: bytes) -> int:
    try:
        return int(codec.walk_dict(body).get(1) or 0)
    except Exception:
        return 0


@dataclass(frozen=True)
class PayMallBuyResult:
    success: bool
    error_code: int = 0


def buy_bundle(client: WSGameClient, bundle_id: int, *, timeout: float = 6.0) -> PayMallBuyResult:
    """Buy one 限時商店/PayMall bundle over pure WS. Idempotent: an already-claimed
    or expired bundle rejects with CMD_ERR and is reported, not raised.
    """
    body = build_pay_mall_buy_body(bundle_id)
    try:
        rc, rb = client.call_for(
            CMD_PAY_MALL_BUY, body,
            expect_cmds=(CMD_PAY_MALL_BUY, CMD_ERR), timeout=timeout)
    except (WSTimeoutError, WSError) as exc:
        logger.warning("pay_mall: bundle_id=%d failed: %s", bundle_id, exc)
        return PayMallBuyResult(success=False)
    if rc == CMD_PAY_MALL_BUY:
        logger.info("pay_mall: bundle_id=%d claimed", bundle_id)
        return PayMallBuyResult(success=True)
    code = _error_code(rb)
    logger.info("pay_mall: bundle_id=%d rejected code=%s (already claimed / expired)",
                bundle_id, code)
    return PayMallBuyResult(success=False, error_code=code)


def claim_free_gift(client: WSGameClient, *, timeout: float = 6.0) -> PayMallBuyResult:
    """每日商店 免費禮包 (150 鑽石/day). Safe to call every run — rejects cleanly
    once already claimed today.
    """
    return buy_bundle(client, FREE_GIFT_BUNDLE_ID, timeout=timeout)


if __name__ == "__main__":
    import sys
    sys.dont_write_bytecode = True
    import os
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from ws_token.creds import load_creds

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    dev = sys.argv[1] if len(sys.argv) > 1 else "7fe98fc6"
    c = WSGameClient(load_creds(dev))
    print("LOGIN", c.connect())
    try:
        result = claim_free_gift(c)
        print("RESULT", result)
    finally:
        c.close()

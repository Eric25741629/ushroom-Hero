# 菇菇武道會 競猜商店 — 競猜幣採購 (pure WS)

> Live-confirmed 2026-06-14 on emulator-5554 (H5, CDP 9230). Implementation:
> `ws_token/kungfu_store.py`; tests `tests/test_kungfu_store.py`. Wired into the WS
> runner as task `kungfu_store` (gated by config `ws_token_kungfu_guess`, default off).

## What it is

菇菇武道會 (client module `kungfu_race`, Task 13 `periodic_tasks.mushroom_arena`,
3-week cycle, 膜拜冠軍) has a 競猜商店 (`KungfuRaceStoreView`). One row group lets
you **buy 競猜幣 (item 1161) with 粉鑽 (currency 2)**. This doc covers automating that
purchase. (The arena PvP 跳戰 `arena.*` module 20 is a *different* feature.)

## Protocol — it's just the Mall

The 競猜商店 is NOT a kungfu-specific cmd. It is the generic Mall (`shop.*` module
27) with `shop_type == 14`. Buying any row sends:

```
shop.shop_buy_c2s {shop_type#1:14, shop_id#2, num#3:1}   cmd 6914 (0x1B02)
  -> shop_buy_s2c {shop_id#1, num#2}                      cmd 6914  (success)
  -> error.error_info_s2c {error_code#1}                  cmd 0x0201 (rejected)
```

Rejection (0x0201) covers: per-period limit reached / 粉鑽 不足 / event window
closed. Verified live by replaying the exact frame the client's `btnBuy` fires
(the free row sent `{shop_type:14, shop_id:15001, num:1}` and granted 100 競猜幣).

Read state (optional): `shop.shop_info_c2s {shop_type#1:14}` cmd 6913 (0x1B01) →
`{shop_type#1, buy_info#2:p_key_value[]}` with `k=shop_id, v=bought_count`. The
module does NOT depend on this — it blind-buys and stops on the first 0x0201.

## The 競猜幣 tiers (from client `configMall`, shop_type 14)

`configMall` row `_data = [shop_id, shop_type, [item_id, qty], [currency_id, price]|null,
"", icon, ?, limit_period, limit_count, [["kungfuRace",[stages]]], null, order, ?]`

| shop_id | 競猜幣 | 花費        | 限購週期 | 上限 | 開放階段 |
|---------|--------|-------------|----------|------|----------|
| 15001   | 100    | 免費        | daily    | 1    | [4,5]    |
| 15002   | 200    | 粉鑽 600    | daily    | 1    | [4,5]    |
| 15003   | 300    | 粉鑽 1500   | weekly   | 2    | [4,5]    |
| 15004   | 500    | 粉鑽 3000   | weekly   | 3    | [4,5]    |

- item **1161 = 競猜幣**; currency **2 = 粉鑽**.
- 開放階段 `[4,5]` = 武道會 循環賽(stage4)/淘汰賽(stage5) — i.e. **膜拜冠軍(慶祝期 stage6)
  的前一周**. Outside this window the rows are hidden client-side and the server
  rejects shop_buy, so the buy is naturally event-gated.
- Buying every tier to cap = **+2400 競猜幣 / −12,600 粉鑽 / 週** (免費100 + 600×1×1 +
  1500×2×1 + 3000×3×1; grants 100+200+300×2+500×3 = 2400).
- rows 15005+ sell 神燈/鑽石金鑰 etc. **for 競猜幣 (currency 1162 — a separate
  token)**; out of scope.

## "X/Y" limit label

The StoreView row shows e.g. `每週限購： 2/2` = **remaining / cap**. After a buy it
decrements (`2/2 → 1/2 → 0/2`). `0/Y` = maxed for the period.

## Scheduling

The server enforces both the open window (循環賽/淘汰賽) and the per-period caps, so
`kungfu_store` is safe to run every WS pass — outside the window every tier rejects
on the first attempt (4 cheap probe calls) and the pass is an idempotent no-op. The
config gate `ws_token_kungfu_guess` (default off) decides *which devices* run it.

## kungfu_race cmd id table (module 65, for reference)

`kungfu_race.*_c2s` ids 16641–16672 (e.g. `kungfu_race_info` 16641, `kungfu_race_bet`
16670 = 押注 team, `kungfu_race_worship` 16665 = 膜拜). The 競猜幣 *purchase* does
NOT use any of these — it is the Mall `shop_buy` above.

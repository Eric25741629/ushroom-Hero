"""讀龍骸聖域 config KV（靜態）。鍵與 accessor 見 DRAGON_REALM_SCHEMA.md。"""
from __future__ import annotations

from dataclasses import dataclass

# config KV index（ActivityLhsyKey，已逆向確認）
KV_ENTER_TIER_TWO_REQUIRE = 7
KV_ENTER_TIER_THREE_REQUIRE = 8
KV_CHEST_KEY = 15
KV_BACK_KILL = 16
KV_STAMINA_TIER = 17
KV_STAMINA_ITEM = 26

_READ_CONFIG_JS = r"""
() => {
  const get = (idx) => {
    try { return window.__dr_getKV(idx); } catch(_) { return null; }
  };
  return {
    tier2: get(7), tier3: get(8), chest: get(15),
    back_kill: get(16), stamina_tier: get(17), stamina_item: get(26),
  };
}
"""


@dataclass(frozen=True)
class DragonConfig:
    tier_two_require: tuple
    tier_three_require: tuple
    stamina_tier: tuple
    back_kill_cooldown: int
    chest_key: dict
    stamina_item_gtid: int


def load_dragon_config(client) -> DragonConfig:
    raw = client._page.evaluate(_READ_CONFIG_JS)  # type: ignore[attr-defined]
    t2 = raw.get("tier2") or [0, 1]
    t3 = raw.get("tier3") or [0, 1]
    chest: dict = {}
    for row in (raw.get("chest") or []):
        # row 形狀見 schema：[.., key_gtid, event_id]
        if len(row) >= 3:
            chest[int(row[2])] = int(row[1])
    return DragonConfig(
        tier_two_require=(int(t2[0]), int(t2[1])),
        tier_three_require=(int(t3[0]), int(t3[1])),
        stamina_tier=tuple(int(x) for x in (raw.get("stamina_tier") or [5, 8, 10])),
        back_kill_cooldown=int(raw.get("back_kill") or 600),
        chest_key=chest,
        stamina_item_gtid=int(raw.get("stamina_item") or 0),
    )

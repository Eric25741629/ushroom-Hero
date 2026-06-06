# FLYPET_PROTO_SCHEMA — 飛寵協議 schema

> 來源:live 匯出自 `netManager.protoRoot`(裝置 7fe98fc6,2026-06-04)。
> 完整欄位定義:同目錄 [`FLYPET_PROTO_SCHEMA.json`](FLYPET_PROTO_SCHEMA.json)(52 messages + 8 shared types)。
> 配套:登入/加密/framing 見 [`AUTH_HANDSHAKE_SPEC.md`](AUTH_HANDSHAKE_SPEC.md)。B(原生 Kotlin)路線編飛寵指令用這份。

## cmd id 公式

```
cmd = 16896 + N      // 16896 = 66 * 256;N = FlyPetControl.send_66_<N> 的 N
```
回應(s2c)與請求(c2s)**同 cmd id**,方向看上下文。

## 關鍵操作對照(中控 `control_panel_app.py` 已用的)

| 中控呼叫 | cmd | 訊息(c2s) | 用途 |
|---------|-----|-----------|------|
| `send_66_3` | 16899 | `fly.fly_egg_incubate_c2s` | 蛋孵化 |
| `send_66_8` | 16904 | `fly.fly_pet_resolve_c2s` | 分解飛寵 |
| `send_66_23` | 16919 | `fly.fly_hybrid_set_shelves_info_c2s` | 設定上架(繁殖位) |
| `send_66_24` | 16920 | `fly.fly_hybrid_partner_shelves_c2s` | 取搭檔上架清單 |
| `send_66_27` | 16923 | `fly.fly_hybrid_start_c2s` | **開始繁殖** `{base_id, fly_a_id, fly_b_id}` |
| `send_66_28` | 16924 | `fly.fly_hybrid_get_c2s` | **收取繁殖結果** |

## 全部飛寵訊息 id(c2s,16897~16934)

```
16897 fly_egg_info            16898 fly_pet_info           16899 fly_egg_incubate
16900 fly_pet_level_up        16901 fly_pet_advance        16902 fly_pet_fight
16903 fly_pet_reset           16904 fly_pet_resolve        16905 fly_pet_rename
16917 fly_hybrid_base_info    16918 fly_hybrid_shelves_info 16919 fly_hybrid_set_shelves_info
16920 fly_hybrid_partner_shelves 16921 fly_hybrid_resp     16922 fly_hybrid_kick
16923 fly_hybrid_start        16924 fly_hybrid_get         16925 fly_hybrid_change_base_name
16926 fly_hybrid_save_setting 16927 fly_hybrid_pet_info    16928 fly_pet_collection
16929 fly_pet_star            16930 fly_pet_lock           16931 fly_pet_resolve_reward
16932 fly_pet_reborn          16933 fly_pet_upgrade_quality 16934 fly_egg_update
```
(另:`task.task_fly_achievement_c2s`=2571、`task.task_fly_achievement_reward_c2s`=2572 屬 task 模組。)

## 主要回應結構(摘,完整看 JSON)

- `fly_pet_info_s2c` → `{ pets: repeated type.p_fly_pet }`(全飛寵清單)
- `fly_hybrid_start_s2c` → `{ base_info: type.p_fly_base }`
- 共用子訊息:`type.p_fly_pet` / `type.p_fly_base` / `type.p_fly_entry` 等 8 個,定義在 JSON 的 `shared_types`。

## 重撈方式(若改版)

```js
const root = netManager.protoRoot;
root.lookup('fly').toJSON();                         // 全 fly.* 訊息
root.lookupType('type.p_fly_pet').toJSON();          // 共用子訊息
(await System.import("chunks:///_virtual/protoregister.ts")).MSG_TO_ID_MAP;  // name→id
```
工具:`tools/_auth_capture_probe.py <port> --await`(CDP eval)。

# 比格先生 / 伴侶 (marry family, module 59) — recon 2026-06-09 CDP 小寶

使用者澄清「比格先生」= **伴侶系統**(送禮給伴侶)。每日/每週動作:
1. **贈禮**:把手上**所有 奶茶 + 鮮花** 通通送給伴侶(99.99% 是第一個伴侶)→ `favor_give_flower`。增親密度。
2. 切磋:與伴侶切磋也增親密度(cmd 未找,先略)。
3. **默契考驗**:伴侶頁有任務,**每週日領一次** → `favor_reward_fetch`。
4. **戒指錘鍊**:戒指那邊有錘鍊,**通通消耗掉真愛之石** → `marry_ring_levup`(loop 到真愛之石歸零)。

## cmds (fake-cnet 抓,module 59;c2s/s2c 共用 id)
- `marry_status` **15105** → s2c {state#1, lover_id#2:uint64, ext#3, his_party#4, lover_info#5:p_common_role}
- `favor_friend_info` **15139** → s2c {left_times#1:int32, page#2, num#3, friend_list#4:p_favor_friend[], reward_times#5:p_key_value[]}
- `favor_give_flower` **15140** c2s {friend_id#1:uint64, flower_id#2:uint64, num#3:uint64} → s2c {update_info#1:p_favor_friend}
- `favor_reward_info` **15141** → s2c {friend_id#1, list#2:p_key_value[], reward_times#3:p_key_value[]}
- `favor_reward_fetch` **15142** c2s {friend_id#1:uint64, favor_lv#2:uint32}
- `favor_buy_flower` 15143、`favor_friend_lv` 15144
- `marry_ring_info` **15134** → s2c {level#1, use#2, exp#3, skin_list#4, skill_use#5, talent_list#6}
- `marry_ring_levup` **15135** c2s {type#1:uint32} → s2c {exp#1, old_lev#2, new_lev#3}
- `marry_ring_use` 15136、`marry_ring_use_skill` 15137

## type
`p_favor_friend {role_id#1:uint64, name#2:string, head#3:p_head, favor_lv#4, favor#5, cur_power#6, gender#7, list#8:p_key_value[]}`
→ 伴侶的 role_id = `friend_id`(give_flower / reward_fetch 用)。

## goods id (configGoods CDP)
- 鮮花 = **1031**、奶茶 = **1106**、真愛之石 = **1114**(春日鮮花 1118 是活動版,不混用)。
- 現量(手上幾個)從 **0x0402 push**(login 時 server 推;見 `ws_token/mining.py` 的 0x0402 解析:
  evt 9800001 consume f3=new_count / 9800009 gain)。或當參數傳入(live-confirm)。

## 注意
- give_flower num#3 = 送幾個。送 all = 讀 1106/1031 現量當 num。flower_id 也是 uint64。
- marry_ring_levup type#1 = 錘鍊類型(可能 1=用真愛之石);loop 升到真愛之石歸零;levup_s2c 回 new_lev。
- favor_reward_fetch favor_lv#2 = 領哪個親密度里程碑的獎(從 favor_reward_info 的 list/reward_times 決定可領的)。
- 失敗一律 0x0201(90 冷卻/159 次數不足/173 活動已結束/2 參數不合法/3 物品不足)→ mutate 走 call_for(cmd,0x0201)。

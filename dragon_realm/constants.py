"""龍骸聖域常數（逆向自客戶端 index.966f5.js，2026-06-04）。"""
from __future__ import annotations

# EventType
PVE = 1
PVP = 2
BOX = 3
TRAP = 4
BUFF = 5
CAVE = 6
MONSTER_TYPES = (PVE, PVP)

# EventDataKey（當前事件 data 陣列的 k）
K_PVE_HP = 1
K_TRAP_TIME = 2
K_BACK_KILL_TIME = 3
K_IS_CHALLENGE = 4
K_ROLE_ID = 5
K_MAX_HP = 6
K_IS_ASK_HELP = 7
K_CENG = 9

# event_choice c2s 的 choice 值
CHOICE_ADVANCE = 1   # 前進 / 擊殺 / (掙扎 — 我方不用)
CHOICE_DETOUR = 2    # 繞路（寶箱無鑰匙）
CHOICE_ASK_HELP = 3  # 求助

# Action kind
A_EXPLORE = "explore"
A_CHOICE = "choice"
A_ENTER_CENG = "enter_ceng"
A_PROVIDE_HELP = "provide_help"
A_RECEIVE_HELP = "receive_help"
A_WAIT = "wait"
A_STOP = "stop"

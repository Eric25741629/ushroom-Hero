import asyncio
import json
import logging
from typing import List, Dict

# 嘗試導入 websockets，如果沒有則提示安裝
try:
    import websockets
except ImportError:
    print("請先安裝 websockets 庫: pip install websockets")
    exit(1)

from miner.planning.smart_planner import plan_smart
from miner.core.config import HIT_TABLE

# 設定 Log
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 前端傳來的數值對應 (參考 index.html getGameState)
# 0: Air/Item -> empty
# 1: Soil -> dirt
# 2: Rock -> rock
# 3: Chest -> reachable_pit (假設寶箱就是礦)
TYPE_MAP = {
    0: "empty",
    1: "dirt",
    2: "rock",
    3: "reachable_pit"
}

# 動作對應回傳給前端的 tool ID
# 0: Pickaxe, 1: Drill, 2: Bomb
TOOL_MAP_REV = {
    "dig": 0,
    "use_drill": 1,
    "use_bomb": 2
}

def convert_grid_to_board(grid_data: List[int], rows: int, cols: int) -> List[List[str]]:
    """將前端的一維陣列轉換為後端二維 board"""
    board = []
    for r in range(rows):
        row = []
        for c in range(cols):
            idx = r * cols + c
            val = grid_data[idx]
            # 簡單轉換：前端只傳來類型，我們假設所有非 0 的都是 reachable
            # 因為前端的 getGameState 已經過濾了視野? 
            # 不，前端只是單純回傳類型。
            # SmartPlanner 需要知道 unreachable。
            # 但這裡我們簡化：假設前端傳來的都是可視範圍內的，
            # 真正不可達的通常被迷霧遮住(前端 exposure 陣列)。
            # 我們可以結合 exposure 陣列來標記 unreachable。
            label = TYPE_MAP.get(val, "rock")
            row.append(label)
        board.append(row)
    return board

def apply_exposure(board: List[List[str]], exposure: List[int], rows: int, cols: int):
    """根據前端的 exposure 資訊，將未探索區域標記為 unreachable"""
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c
            if exposure[idx] == 0: # 0 = 未暴露/迷霧
                # 無論實體還是空地，未暴露都應加上前綴
                if not board[r][c].startswith("unreachable_"):
                    board[r][c] = "unreachable_" + board[r][c]
    return board

def get_visual_board(board: List[List[str]]) -> str:
    """將 board 轉為視覺化的符號字串，增加座標軸"""
    symbols = {
        "empty": ".",
        "void": ".",
        "dug_pit": ".",
        "unreachable_empty": "_",
        "unreachable_void": "_",
        "dirt": "D",
        "unreachable_dirt": "d",
        "rock": "R",
        "unreachable_rock": "r",
        "reachable_pit": "*",
        "unreachable_pit": "X"
    }
    
    C = len(board[0])
    header = "   " + " ".join([str(i) for i in range(C)])
    rows = [header]
    
    for i, row in enumerate(board):
        row_str = f"{i:2d} " + " ".join([symbols.get(cell, cell[:1]) for cell in row])
        rows.append(row_str)
    return "\n".join(rows)

async def handler(websocket, *args):
    logger.info("前端已連線")
    try:
        async for message in websocket:
            data = json.loads(message)
            
            # 解析前端資料
            grid_raw = data.get("grid", [])
            exposure_raw = data.get("exposure", [])
            rows = data.get("rows", 7)
            cols = data.get("cols", 6)
            resources = data.get("resources", [999, 0, 0])
            
            # 轉換盤面
            board = convert_grid_to_board(grid_raw, rows, cols)
            board = apply_exposure(board, exposure_raw, rows, cols)
            
            # 強制截斷到 7 層 (只關注可視範圍)
            # 這能避免 AI 為了還沒出現的深層目標而提前浪費道具
            VISIBLE_ROWS = 7
            if len(board) > VISIBLE_ROWS:
                board = board[:VISIBLE_ROWS]
            
            # 視覺化輸出
            logger.info(f"收到盤面 (截斷後):\n{get_visual_board(board)}")
            
            # 準備道具庫存
            items = {'drill': resources[1], 'bomb': resources[2]}
            shovels = resources[0]
            
            logger.info(f"道具: {items}, 鎬子: {shovels}")
            
            # 呼叫 Smart Planner
            # 注意：前端的可視範圍通常比實際 7x6 大 (rows 可能是 10)，這對 Planner 更好
            start_time = asyncio.get_event_loop().time()
            
            # 因為是同步函數，跑在 executor 中避免卡住 WS
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: plan_smart(board, shovels, items))
            
            duration = asyncio.get_event_loop().time() - start_time
            logger.info(f"規劃耗時: {duration:.3f}s, 結果: {result.get('ok')}")
            
            if result.get("ok") and result.get("steps"):
                first_step = result["steps"][0] # 只取第一步
                
                # 轉換為前端指令
                action_type = first_step['type']
                r, c = first_step['pos']
                
                tool_id = 0 # Default Pickaxe
                if action_type == 'use':
                    item_name = first_step.get('item', 'drill')
                    tool_id = TOOL_MAP_REV.get(f"use_{item_name}", 0)
                
                response = {
                    "r": r,
                    "c": c,
                    "tool": tool_id
                }
                
                logger.info(f"回傳動作: {response} ({first_step})")
                await websocket.send(json.dumps(response))
            else:
                remaining = result.get("remaining_pits", "?")
                f7 = result.get("floor7_open", False)
                if f7 and remaining == 0:
                    logger.info("任務已完成 (第7層已通，無剩餘礦物)，等待下樓...")
                else:
                    logger.warning(f"無法規劃路徑. 剩餘礦物: {remaining}, 第7層已通: {f7}")
                
                # 發送一個空動作回前端，以維持通訊迴圈
                # 前端 JSON.parse 會成功，但找不到 r, c 會忽略動作
                await websocket.send(json.dumps({"status": "wait"}))

    except websockets.exceptions.ConnectionClosed:
        logger.info("前端斷線")
    except Exception as e:
        logger.error(f"發生錯誤: {e}", exc_info=True)

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        logger.info("AI Server 啟動於 ws://localhost:8765")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())

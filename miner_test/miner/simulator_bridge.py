import asyncio
import json
import websockets
from miner.planning.smart_planner import plan_smart, PlannerConfig
import logging
import os

# 配置日誌
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SimulatorBridge")

CONFIG_FILE = "miner/planner_config.json"

def load_config():
    config = PlannerConfig()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                for k, v in data.items():
                    setattr(config, k, v)
            logger.info(f"已從 {CONFIG_FILE} 載入配置")
        except Exception as e:
            logger.error(f"載入配置失敗: {e}")
    return config

# 全局統計
stats = {
    "total_steps": 0,
    "total_cost": 0.0,
    "sessions": 0
}

async def handler(websocket):
    logger.info("模拟器已连接")
    stats["sessions"] += 1
    config = load_config()
    
    try:
        async for message in websocket:
            data = json.loads(message)
            # ... (解析逻辑相同)
            grid = data.get('grid')
            exposure = data.get('exposure')
            rows = data.get('rows')
            cols = data.get('cols')
            resources = data.get('resources') # [pickaxe, drill, bomb]
            
            shovels = resources[0]
            items = {'drill': resources[1], 'bomb': resources[2]}
            
            board = []
            for r in range(rows):
                row_labels = []
                for c in range(cols):
                    idx = r * cols + c
                    g_val = grid[idx]
                    e_val = exposure[idx]
                    label = "empty"
                    if g_val == 1: label = "dirt"
                    elif g_val == 2: label = "rock"
                    elif g_val == 3: label = "pit"
                    if e_val == 0: label = f"unreachable_{label}"
                    row_labels.append(label)
                board.append(row_labels)

            # 调用 A* 算法，傳入最新配置
            result = plan_smart(board, shovels=shovels, items=items, config=config)
            
            if result.get("ok") and result.get("steps"):
                next_step = result["steps"][0]
                stats["total_steps"] += 1
                stats["total_cost"] += next_step.get("step_cost", 0)
                
                response = {
                    "r": next_step["pos"][0],
                    "c": next_step["pos"][1],
                    "tool": 0
                }
                if next_step["type"] == "use":
                    if next_step["item"] == "drill": response["tool"] = 1
                    elif next_step["item"] == "bomb": response["tool"] = 2
                
                await websocket.send(json.dumps(response))
                
                if stats["total_steps"] % 10 == 0:
                    logger.info(f"效能摘要: 累計步數={stats['total_steps']}, 累計成本={stats['total_cost']:.2f}")
            else:
                logger.info("無可行路徑，可能需要手動介入或重新生成")
                
    except websockets.exceptions.ConnectionClosedOK:
        logger.info("模拟器连接正常关闭")
    except Exception as e:
        logger.error(f"处理消息时出错: {e}", exc_info=True)

async def main():
    async with websockets.serve(handler, "localhost", 8765):
        logger.info("WebSocket 服务器启动在 ws://localhost:8765")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())

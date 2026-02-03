# Runner to start SB3 PPO training for miner environment (10M timesteps)
from miner.rl_train import MinerEnv, train_sb3_ppo

board = [
    ["unreachable_dirt","rock","empty","rock","unreachable_empty","unreachable_dirt"],
    ["dirt","empty","empty","dirt","unreachable_dirt","unreachable_rock"],
    ["dirt","empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty"],
    ["empty","empty","rock","unreachable_rock","unreachable_dirt","unreachable_empty"],
    ["empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty","unreachable_dirt"],
    ["empty","dirt","unreachable_empty","unreachable_pit","unreachable_dirt","unreachable_empty"],
    ["rock","unreachable_rock","unreachable_dirt","unreachable_dirt","unreachable_dirt","unreachable_rock"],
]

env = MinerEnv(board, pickaxes=50, items={"drill":2, "bomb":2})

print("Starting SB3 PPO training: 10,000,000 timesteps")
try:
    model = train_sb3_ppo(env, timesteps=10_000_000)
    print("Training completed, model saved as miner_ppo.zip")
except Exception as e:
    print("Training failed:", e)
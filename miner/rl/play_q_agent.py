# Play episodes using saved Q-table policy (miner_q_table.pkl)
import pickle
import random
from miner.rl_train import MinerEnv, state_to_str
from stable_baselines3.common.env_util import make_vec_env

def load_q_table(path="miner_q_table.pkl"):
    with open(path, "rb") as f:
        return pickle.load(f)

def action_to_str(a, R, C):
    act_type = a // (R * C)
    idx = a % (R * C)
    r = idx // C
    c = idx % C
    if act_type == 0:
        return f"dig {(r,c)}"
    elif act_type == 1:
        return f"use drill {(r,c)}"
    else:
        return f"use bomb {(r,c)}"

def play_q_policy(board, q_table, episodes=5, pickaxes=50, items=None):
    env = MinerEnv(board, pickaxes=pickaxes, items=items or {"drill":0,"bomb":0})
    for ep in range(1, episodes+1):
        s = env.reset()
        # gym/gymnasium compatibility: reset may return (obs,info)
        if isinstance(s, tuple):
            s = s[0]
        s_str = state_to_str(s)
        done = False
        total_reward = 0.0
        steps = 0
        print(f"\n=== Episode {ep} ===")
        env.render()
        while not done and steps < 200:
            if s_str in q_table:
                q_row = q_table[s_str]
                a = int(max(range(len(q_row)), key=lambda x: q_row[x]))
            else:
                a = env.action_space.sample()
            out = env.step(a)
            # support Gymnasium (obs, reward, terminated, truncated, info)
            if len(out) == 5:
                s2, r, terminated, truncated, info = out
                done = bool(terminated or truncated)
            else:
                s2, r, done, info = out
            if isinstance(s2, tuple):
                s2 = s2[0]
            print(f"step {steps}: action={action_to_str(a, env.R, env.C)} reward={r} info={info}")
            total_reward += float(r)
            steps += 1
            s_str = state_to_str(s2)
        print(f"Episode {ep} finished steps={steps} total_reward={total_reward}")
        env.render()

if __name__ == "__main__":
    sample_board = [
        ["unreachable_dirt","rock","empty","rock","unreachable_empty","unreachable_dirt"],
        ["dirt","empty","empty","dirt","unreachable_dirt","unreachable_rock"],
        ["dirt","empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty"],
        ["empty","empty","rock","unreachable_rock","unreachable_dirt","unreachable_empty"],
        ["empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty","unreachable_dirt"],
        ["empty","dirt","unreachable_empty","unreachable_pit","unreachable_dirt","unreachable_empty"],
        ["rock","unreachable_rock","unreachable_dirt","unreachable_dirt","unreachable_dirt","unreachable_rock"],
    ]
    try:
        q = load_q_table("miner_q_table.pkl")
    except Exception:
        q = {}
        print("Q-table not found, will act randomly.")
    play_q_policy(sample_board, q, episodes=5, pickaxes=20, items={"drill":1,"bomb":1})
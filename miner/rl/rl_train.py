# RL training wrapper and simple Q-learning / SB3 skeleton for miner
import gymnasium as gym
import random
import pickle
from typing import Tuple, List, Dict, Any, Optional
from miner.scripts.simulator import GameSimulator
from copy import deepcopy

LABEL2INT = {
    "empty": 0,
    "dug_pit": 0,
    "void": 0,
    "dirt": 1,
    "one_hit_rock": 1,
    "rock": 2,
    "pit": 3,
    "reachable_pit": 3,
    "unreachable_pit": 4,
    "unreachable_empty": 0,
    "unreachable_dirt": 1,
    "unreachable_rock": 2,
}

class MinerEnv(gym.Env):
    metadata = {"render.modes": ["human"]}

    def __init__(self, board: List[List[str]], pickaxes: int = 20, items: Dict[str,int] = None):
        super().__init__()
        self.board_template = deepcopy(board)
        self.R = len(board)
        self.C = len(board[0])
        self.sim = GameSimulator(self.board_template, pickaxes=pickaxes, items=items)
        # actions: 0..(R*C-1) = dig at idx
        # R*C..2*R*C-1 = use_drill at idx
        # 2*R*C..3*R*C-1 = use_bomb at idx
        self.action_size = 3 * self.R * self.C
        self.action_space = gym.spaces.Discrete(self.action_size)
        self.observation_space = gym.spaces.Box(low=0, high=5, shape=(self.R, self.C), dtype=int)

    def _obs(self):
        out = [[LABEL2INT.get(cell, 0) for cell in row] for row in self.sim.board]
        import numpy as np
        return np.array(out, dtype=int)

    def reset(self, *, seed: Optional[int] = None, options: Optional[dict] = None):
        # gymnasium API compatibility: accept seed and options, return (obs, info)
        try:
            super().reset(seed=seed)
        except TypeError:
            # older gym compatibility
            pass
        self.sim.reset()
        return self._obs(), {}

    def step(self, action: int):
        act_type = action // (self.R * self.C)
        idx = action % (self.R * self.C)
        r = idx // self.C
        c = idx % self.C
        if act_type == 0:
            a = {"type":"dig","pos":(r,c)}
        elif act_type == 1:
            a = {"type":"use","item":"drill","pos":(r,c)}
        else:
            a = {"type":"use","item":"bomb","pos":(r,c)}
        obs, reward, done, info = self.sim.step(a)
        # Gymnasium expects (obs, reward, terminated, truncated, info)
        terminated = bool(done)
        truncated = False
        return self._obs(), float(reward), terminated, truncated, info

    def render(self, mode="human"):
        self.sim.render()

def state_to_str(state):
    return str(state.tolist())

def train_q_learning(env: MinerEnv, episodes: int = 2000, alpha=0.1, gamma=0.99,
                     eps_start=1.0, eps_end=0.1, eps_decay=0.995):
    q: Dict[str, List[float]] = {}
    eps = eps_start
    for ep in range(episodes):
        s = env.reset()
        s_str = state_to_str(s)
        q.setdefault(s_str, [0.0]*env.action_space.n)
        total = 0.0
        done = False
        while not done:
            if random.random() < eps:
                a = env.action_space.sample()
            else:
                a = int(max(range(env.action_space.n), key=lambda x: q.setdefault(s_str, [0.0]*env.action_space.n)[x]))
            s2, r, done, info = env.step(a)
            s2_str = state_to_str(s2)
            q.setdefault(s2_str, [0.0]*env.action_space.n)
            best_next = max(q[s2_str])
            q[s_str][a] += alpha * (r + gamma * best_next - q[s_str][a])
            s_str = s2_str
            total += r
        eps = max(eps_end, eps * eps_decay)
        if (ep+1) % 100 == 0:
            print(f"EP {ep+1}/{episodes} reward={total:.1f} eps={eps:.3f}")
    with open("miner_q_table.pkl", "wb") as f:
        pickle.dump(q, f)
    print("Saved Q-table -> miner_q_table.pkl")
    return q

def train_sb3_ppo(env: MinerEnv, timesteps: int = 10000):
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_checker import check_env
    except Exception as e:
        print("stable-baselines3 not installed:", e)
        return
    check_env(env)
    model = PPO("MlpPolicy", env, verbose=1)
    model.learn(total_timesteps=timesteps)
    model.save("miner_ppo")
    print("Saved PPO model -> miner_ppo.zip")
    return model

if __name__ == "__main__":
    # quick demo: train Q-learning on sample board
    sample_board = [
        ["unreachable_dirt","rock","empty","rock","unreachable_empty","unreachable_dirt"],
        ["dirt","empty","empty","dirt","unreachable_dirt","unreachable_rock"],
        ["dirt","empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty"],
        ["empty","empty","rock","unreachable_rock","unreachable_dirt","unreachable_empty"],
        ["empty","rock","unreachable_dirt","unreachable_dirt","unreachable_empty","unreachable_dirt"],
        ["empty","dirt","unreachable_empty","unreachable_pit","unreachable_dirt","unreachable_empty"],
        ["rock","unreachable_rock","unreachable_dirt","unreachable_dirt","unreachable_dirt","unreachable_rock"],
    ]
    env = MinerEnv(sample_board, pickaxes=200, items={"drill":1,"bomb":1})
    q = train_q_learning(env, episodes=10000000)
    print("Done")
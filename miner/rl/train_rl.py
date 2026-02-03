import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.optim as optim


LOG_FILE = os.path.join(os.path.dirname(__file__), "rl_logs", "events.jsonl")
BOARD_SHAPE = (7, 6)  # 與 Mining.py 一致

ACTION_INDEX = {
    "mine": 0,
    "collect": 1,
    "mine_path": 2,
    "descend": 3,
}


def load_events(path: str) -> List[Dict]:
    if not os.path.exists(path):
        print(f"[train_rl] No log file at {path}")
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def flatten_board(board: Sequence[Sequence[str]]) -> List[str]:
    return [cell for row in board for cell in row]


def build_label_mapping(events: Sequence[Dict]) -> Dict[str, int]:
    label_to_idx = {"<pad>": 0}
    for evt in events:
        for key in ("board_before", "board_after"):
            board = evt.get(key)
            if not board:
                continue
            for cell in flatten_board(board):
                if cell not in label_to_idx:
                    label_to_idx[cell] = len(label_to_idx)
        for cell_evt in evt.get("cell_events", []):
            lbl = cell_evt.get("label_before")
            if lbl and lbl not in label_to_idx:
                label_to_idx[lbl] = len(label_to_idx)
    return label_to_idx


def encode_board(board: Optional[Sequence[Sequence[str]]], label_to_idx: Dict[str, int]) -> torch.Tensor:
    if not board:
        # 空盤面視為 0（pad）
        return torch.zeros(BOARD_SHAPE[0] * BOARD_SHAPE[1], dtype=torch.long)
    flat_labels = flatten_board(board)
    idxs = [label_to_idx.get(lbl, 0) for lbl in flat_labels]
    return torch.tensor(idxs, dtype=torch.long)


@dataclass
class Transition:
    state: torch.Tensor
    action: int
    reward: float
    next_state: torch.Tensor
    done: bool


def build_transitions(events: Sequence[Dict], label_to_idx: Dict[str, int]) -> Tuple[List[List[Transition]], Counter]:
    episodes: List[List[Transition]] = []
    current_episode: List[Transition] = []
    action_counter: Counter = Counter()

    for evt in events:
        action_name = evt.get("plan_action")
        if action_name not in ACTION_INDEX:
            continue
        state_tensor = encode_board(evt.get("board_before"), label_to_idx)
        next_state_tensor = encode_board(evt.get("board_after"), label_to_idx)
        cost = float(evt.get("step_cost_expected") or 0.0)
        gain = float(evt.get("gain_expected") or 0.0)
        reward = gain - cost
        done = bool(evt.get("terminated")) or (action_name == "descend")
        transition = Transition(
            state=state_tensor,
            action=ACTION_INDEX[action_name],
            reward=reward,
            next_state=next_state_tensor,
            done=done,
        )
        current_episode.append(transition)
        action_counter[action_name] += 1
        if done:
            if current_episode:
                episodes.append(current_episode)
            current_episode = []

    if current_episode:
        episodes.append(current_episode)

    if not episodes:
        print("[train_rl] No complete episodes found; training will be skipped.")
    else:
        print(f"[train_rl] Built {len(episodes)} offline episodes")
    return episodes, action_counter


class PolicyValueNet(nn.Module):
    def __init__(self, vocab_size: int, embedding_dim: int, hidden_dim: int, num_actions: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=0)
        input_dim = BOARD_SHAPE[0] * BOARD_SHAPE[1] * embedding_dim
        self.policy_net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, num_actions),
        )
        self.value_net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, state_tokens: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        # state_tokens: (batch, cells)
        emb = self.embedding(state_tokens)  # (batch, cells, emb)
        flat = emb.reshape(emb.size(0), -1)
        logits = self.policy_net(flat)
        values = self.value_net(flat).squeeze(-1)
        return logits, values


def compute_returns(episode: Sequence[Transition], gamma: float) -> torch.Tensor:
    returns = []
    running = 0.0
    for transition in reversed(episode):
        running = transition.reward + gamma * running
        returns.append(running)
    returns.reverse()
    return torch.tensor(returns, dtype=torch.float32)


def train_offline_actor_critic(
    episodes: Sequence[Sequence[Transition]],
    label_to_idx: Dict[str, int],
    device: str,
    epochs: int,
    lr: float,
    gamma: float,
    value_coef: float,
    entropy_coef: float,
) -> PolicyValueNet:
    model = PolicyValueNet(
        vocab_size=len(label_to_idx),
        embedding_dim=32,
        hidden_dim=256,
        num_actions=len(ACTION_INDEX),
    ).to(device)
    optimizer = optim.Adam(model.parameters(), lr=lr)

    for epoch in range(1, epochs + 1):
        total_policy_loss = 0.0
        total_value_loss = 0.0
        total_entropy = 0.0
        total_steps = 0

        for episode in episodes:
            returns = compute_returns(episode, gamma).to(device)
            states = torch.stack([t.state for t in episode]).to(device)
            actions = torch.tensor([t.action for t in episode], dtype=torch.long, device=device)

            logits, values = model(states)
            log_probs = torch.log_softmax(logits, dim=-1)
            probs = torch.softmax(logits, dim=-1)
            action_log_probs = log_probs.gather(1, actions.unsqueeze(1)).squeeze(1)

            advantages = returns - values.detach()
            policy_loss = -(action_log_probs * advantages).mean()
            value_loss = nn.functional.mse_loss(values, returns)
            entropy = -(log_probs * probs).sum(dim=1).mean()

            loss = policy_loss + value_coef * value_loss - entropy_coef * entropy
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            total_policy_loss += policy_loss.item() * len(episode)
            total_value_loss += value_loss.item() * len(episode)
            total_entropy += entropy.item() * len(episode)
            total_steps += len(episode)

        if total_steps == 0:
            break
        avg_policy = total_policy_loss / total_steps
        avg_value = total_value_loss / total_steps
        avg_entropy = total_entropy / total_steps
        print(
            f"[train_rl] Epoch {epoch:02d} | policy={avg_policy:.4f} | value={avg_value:.4f} | entropy={avg_entropy:.4f}"
        )

    return model


def save_policy(model: PolicyValueNet, label_to_idx: Dict[str, int], output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    checkpoint = {
        "state_dict": model.state_dict(),
        "label_to_idx": label_to_idx,
        "action_index": ACTION_INDEX,
        "board_shape": BOARD_SHAPE,
    }
    output_path = os.path.join(output_dir, "offline_actor_critic.pth")
    torch.save(checkpoint, output_path)
    print(f"[train_rl] Saved policy checkpoint to {output_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train offline RL policy from Miner logs")
    parser.add_argument("--log-file", default=LOG_FILE, help="Path to events.jsonl")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--gamma", type=float, default=0.95)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--value-coef", type=float, default=0.5)
    parser.add_argument("--entropy-coef", type=float, default=0.01)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "rl_checkpoints"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    events = load_events(args.log_file)
    if not events:
        print("[train_rl] No events to train on yet.")
        return

    label_to_idx = build_label_mapping(events)
    episodes, action_counter = build_transitions(events, label_to_idx)
    if not episodes:
        return

    print("[train_rl] Action distribution:")
    for act, cnt in action_counter.most_common():
        print(f"  {act:>10}: {cnt}")

    model = train_offline_actor_critic(
        episodes=episodes,
        label_to_idx=label_to_idx,
        device=args.device,
        epochs=args.epochs,
        lr=args.lr,
        gamma=args.gamma,
        value_coef=args.value_coef,
        entropy_coef=args.entropy_coef,
    )

    save_policy(model, label_to_idx, args.output_dir)


if __name__ == "__main__":
    main()

import json
import os
import threading
import time
import subprocess
from typing import Any, Dict, List, Optional


class RLRecorder:
    """Lightweight experience logger with optional background training trigger."""

    def __init__(
        self,
        log_dir: str,
        auto_train: bool = False,
        train_command: Optional[List[str]] = None,
        train_interval: float = 300.0,
        flush_interval: int = 1,
    ) -> None:
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = os.path.join(self.log_dir, "events.jsonl")
        self.meta_path = os.path.join(self.log_dir, "meta.json")
        self.auto_train = auto_train and bool(train_command)
        self.train_command = train_command
        self.train_interval = train_interval
        self.flush_interval = max(1, flush_interval)

        self._lock = threading.Lock()
        self._buffer: List[Dict[str, Any]] = []
        self._event_count = 0
        self._last_train_ts = time.time()
        self._training_thread: Optional[threading.Thread] = None

        # Persist simple metadata for reproducibility
        if not os.path.exists(self.meta_path):
            meta = {
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "train_command": self.train_command,
                "auto_train": self.auto_train,
                "train_interval": self.train_interval,
            }
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    def record_transition(self, event: Dict[str, Any]) -> None:
        """Append a transition event (already JSON-serializable)."""
        event.setdefault("timestamp", time.time())
        with self._lock:
            self._buffer.append(event)
            self._event_count += 1
            if len(self._buffer) >= self.flush_interval:
                self._flush_locked()
                self._maybe_trigger_training_locked()

    # ------------------------------------------------------------------
    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    # ------------------------------------------------------------------
    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        with open(self.log_path, "a", encoding="utf-8") as f:
            for evt in self._buffer:
                json.dump(evt, f, ensure_ascii=False)
                f.write("\n")
        self._buffer.clear()

    # ------------------------------------------------------------------
    def _maybe_trigger_training_locked(self) -> None:
        if not self.auto_train or not self.train_command:
            return
        now = time.time()
        if self._training_thread and self._training_thread.is_alive():
            return
        if now - self._last_train_ts < self.train_interval:
            return
        self._last_train_ts = now
        self._training_thread = threading.Thread(target=self._run_training, daemon=True)
        self._training_thread.start()

    # ------------------------------------------------------------------
    def _run_training(self) -> None:
        try:
            subprocess.run(self.train_command, cwd=self.log_dir, check=False)
        except Exception as exc:  # pragma: no cover - best effort logging
            print(f"[RLRecorder] Training command failed: {exc}")

    # ------------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "queued": len(self._buffer),
                "total": self._event_count,
                "log_path": self.log_path,
                "auto_train": self.auto_train,
            }

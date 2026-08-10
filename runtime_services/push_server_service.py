import os
import socket
import subprocess
import sys


_push_server_proc = None


def is_tcp_port_open(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def ensure_push_server_started(base_dir: str) -> bool:
    """Auto-start push_project/server/app.py if push server is not already running."""
    global _push_server_proc
    host = "127.0.0.1"
    port = int(os.environ.get("PUSH_SERVER_PORT", "5000"))

    if is_tcp_port_open(host, port):
        print(f"[System] Push server already running at http://{host}:{port}")
        return True

    server_dir = os.path.join(base_dir, "push_project", "server")
    server_app = os.path.join(server_dir, "app.py")

    if not os.path.exists(server_app):
        print(f"[Warn] push server app not found: {server_app}")
        return False

    env = os.environ.copy()
    env.setdefault("PORT", str(port))

    try:
        _push_server_proc = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=server_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[System] Push server launched (pid={_push_server_proc.pid}) at http://{host}:{port}")
    except Exception as e:
        print(f"[Warn] Failed to launch push server: {e}")
        return False

    return True

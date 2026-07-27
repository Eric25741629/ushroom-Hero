# -*- coding: utf-8 -*-
"""萬神試煉 WS 協議 recon — 只讀不打。

連接小寶 7fe98fc6 CDP 9226，用 WS 讀取：
- rogue_info_c2s (0x4c01) → point/score 確認開放
- rogue_status_c2s (0x4c20) → status 值（0=無進行中/1=有？）

不發 rogue_main_enter（會真開局扣次數）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add repo root to path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ws_token.creds import load_creds
from ws_token.client import WSGameClient
from ws_token import codec

CMD_ROGUE_INFO = 0x4C01
CMD_ROGUE_STATUS = 0x4C20

DEVICE = "7fe98fc6"


def main() -> int:
    import argparse
    from pathlib import Path as _Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=DEVICE)
    ap.add_argument("--auth-dir", default=None, help="覆寫 auth_state 路徑（worktree 跑時用）")
    args = ap.parse_args()

    device = args.device
    auth_dir_kw = {}
    if args.auth_dir:
        auth_dir_kw["auth_dir"] = _Path(args.auth_dir)

    print(f"[recon] load creds for {device}")
    try:
        creds = load_creds(device, **auth_dir_kw)
    except FileNotFoundError as e:
        print(f"[recon] 錯誤: {e}")
        return 1

    print(f"[recon] ws_url: {creds.ws_url[:60]}...")
    print(f"[recon] role_id: {creds.role_id}")

    client = WSGameClient(creds)
    try:
        print(f"\n[recon] connect + login...")
        login_fields = client.connect()
        print(f"[recon] login ok: {login_fields}")

        # 1. rogue_info_c2s (0x4c01)
        print(f"\n[recon] === rogue_info_c2s (0x{CMD_ROGUE_INFO:04x}) ===")
        try:
            info_body = client.call(CMD_ROGUE_INFO, b"", timeout=10.0)
            info_dict = codec.walk_dict(info_body)
            print(f"[recon] rogue_info fields: {info_dict}")
            # 典型欄位：point（試煉之心）、score（分數）、開放狀態等
        except Exception as e:
            print(f"[recon] rogue_info 失敗: {e}")

        # 2. rogue_status_c2s (0x4c20)
        print(f"\n[recon] === rogue_status_c2s (0x{CMD_ROGUE_STATUS:04x}) ===")
        try:
            status_body = client.call(CMD_ROGUE_STATUS, b"", timeout=10.0)
            status_dict = codec.walk_dict(status_body)
            print(f"[recon] rogue_status fields: {status_dict}")
            # 期待欄位：status（0=無進行中/1=有？）
            status_val = status_dict.get(1)  # 猜測 field 1 = status
            if status_val is not None:
                print(f"[recon] status value (field 1): {status_val}")
            else:
                print(f"[recon] status value 未找到（可能在其他 field）")
        except Exception as e:
            print(f"[recon] rogue_status 失敗: {e}")

        print(f"\n[recon] === recon 完成，不發 rogue_main_enter ===")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

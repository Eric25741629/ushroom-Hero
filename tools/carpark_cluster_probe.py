"""唯讀採樣：跨界泊銀 lot 占用者的 role attrs，驗證「同服」判定欄位。

跨界窗口（台灣 10:00-22:00）內跑，lot 有人時才有資料：

  python tools/carpark_cluster_probe.py [device]

印出每個有占用的泊銀 lot：占用者 attrs（info_list#6 + ext#8 kv 攤平）與
count_same_server(本服 server_id) 結果。預期同服占用者的某個 attr 值 ==
登入回包的 server_id（小寶=1467）；若不是，從輸出找出正確欄位後修
carpark.count_same_server。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ws_token import carpark  # noqa: E402
from ws_token.client import WSGameClient  # noqa: E402
from ws_token.creds import load_creds  # noqa: E402


def main() -> int:
    dev = sys.argv[1] if len(sys.argv) > 1 else "7fe98fc6"
    client = WSGameClient(load_creds(dev))
    login = client.connect()
    server_id = int(login.get("server_id") or 0)
    print(f"[probe] login code={login['code']} server_id={server_id}", flush=True)
    try:
        lots = carpark.read_cross_null_spaces(client)
        silver = [l for l in lots if carpark.is_silver_ceng(l.ceng)
                  and l.null_num < 10]  # only lots with occupants
        print(f"[probe] {len(silver)} silver lot(s) with occupants", flush=True)
        for lot in silver[:12]:
            d = carpark.read_lot(client, type=carpark.CROSS_TYPE,
                                 master_id=lot.master_id, ceng=lot.ceng)
            same = carpark.count_same_server(d, server_id)
            lv = lot.ceng - carpark.SILVER_CENG_BASE + 1
            print(f"[probe] 鉑銀{lv} master={lot.master_id} "
                  f"occupied={10 - lot.null_num} same_server={same}", flush=True)
            for s in d.spaces:
                if s.occupied:
                    print(f"    pos={s.pos} role={s.role_id} attrs={s.attrs}",
                          flush=True)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""One-shot: test 0x1602 free ad-summon (skill slot=8, companion slot=7).

Usage:
  python tools/probe_free_draw.py [device] [slot]
  slot: 8=技能, 7=同伴 (default 8)
"""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, ".")

from ws_token.creds import load_creds
from ws_token.client import WSGameClient
from ws_token import codec

device = sys.argv[1] if len(sys.argv) > 1 else "emulator-5556"
slot   = int(sys.argv[2]) if len(sys.argv) > 2 else 8
print(f"device={device}  slot={slot}  ({'技能' if slot==8 else '同伴'})")

CMD_FREE_DRAW  = 0x1602  # 5634 — free ad-summon
CMD_DRAW       = 0x0902  # 2306 — server pushes draw result on this
CMD_ERROR      = 0x0201

creds = load_creds(device)
client = WSGameClient(creds)
client.connect()
time.sleep(1.5)

# body: {field1=slot, field3=1}
body = codec.pb_uint(1, slot) + codec.pb_uint(3, 1)
print(f"TX body hex: {body.hex()}")

try:
    reply_cmd, reply_body = client.call_for(
        CMD_FREE_DRAW, body,
        expect_cmds=(CMD_DRAW, CMD_ERROR),
        timeout=15,
    )
    print(f"Reply cmd: 0x{reply_cmd:04x} ({reply_cmd})")
    print(f"Reply body len: {len(reply_body)} bytes")
    d = codec.walk_dict(reply_body)
    print(f"Top-level fields: {list(d.keys())}")
    # count field-2 groups (draws)
    drawn = sum(1 for fnum, v in codec.walk(reply_body)
                if fnum == 2 and isinstance(v, (bytes, bytearray)))
    print(f"Drawn items (field#2 count): {drawn}")
    if reply_cmd == CMD_ERROR:
        ec = d.get(1)
        print(f"ERROR code: {ec}")
except Exception as e:
    import traceback; traceback.print_exc()
finally:
    client.close()
    print("done")

"""One-shot: query draw.draw_info (cmd 2305) and dump pool_list fields."""
import sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, ".")

from ws_token.creds import load_creds
from ws_token.client import WSGameClient
from ws_token import codec

device = sys.argv[1] if len(sys.argv) > 1 else "emulator-5556"
print(f"device: {device}")
creds = load_creds(device)
client = WSGameClient(creds)
client.connect()
time.sleep(1.5)

CMD_DRAW_INFO = 2305  # draw.draw_info  c2s {}

print("=== draw_info (2305) ===")
try:
    body = client.call(CMD_DRAW_INFO, b"", timeout=10)
    print(f"body len={len(body)} bytes")
    for i, (fnum, v) in enumerate(codec.walk(body)):
        if isinstance(v, (bytes, bytearray)):
            inner = codec.walk_dict(v)
            print(f"  pool[{i}] field#{fnum} => {inner}")
            # go one level deeper for nested sub-messages
            for f2, v2 in codec.walk(v):
                if isinstance(v2, (bytes, bytearray)):
                    inner2 = codec.walk_dict(v2)
                    print(f"    sub field#{f2} => {inner2}")
        else:
            print(f"  scalar field#{fnum}: {v}")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"ERROR: {e}")
finally:
    client.close()
    print("done")

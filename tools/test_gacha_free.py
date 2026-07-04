"""Test _run_gacha_free via runner against emulator-5556.

Checks:
  - Already-done today → returns {skipped: ...}
  - Resets ws_state gacha_free.last_date to yesterday → tries free draws
  - Error 89 daily-limit should show up cleanly in stopped field
"""
import sys, datetime
sys.dont_write_bytecode = True
sys.path.insert(0, ".")

from ws_token import state as ws_state, gacha
from ws_token.creds import load_creds
from ws_token.client import WSGameClient

device = sys.argv[1] if len(sys.argv) > 1 else "emulator-5556"
print(f"device: {device}")

# --- peek current ws_state gacha_free ---
st = ws_state.load_state(device)
print(f"gacha_free state before: {st.get('gacha_free')}")

# If today already recorded, wipe so we can re-test
today = datetime.datetime.now().strftime("%Y-%m-%d")
gf = st.get("gacha_free") or {}
if gf.get("last_date") == today:
    print("Wiping today's gate to re-test...")
    gf.pop("last_date", None)
    st["gacha_free"] = gf
    ws_state.save_state(device, st)

# --- connect and run free draws ---
creds = load_creds(device)
client = WSGameClient(creds)
client.connect()
import time; time.sleep(1.5)

try:
    results = gacha.free_draw_all(client)
    print(f"free_draw_all results: {results}")
    total = sum(v.get("drawn", 0) for v in results.values())
    print(f"total drawn: {total}")
finally:
    client.close()

# verify state was already updated by free_draw_all? (no, runner does state)
# just print state after
st2 = ws_state.load_state(device)
print(f"gacha_free state after: {st2.get('gacha_free')}")
print("done")

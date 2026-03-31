"""
monitor.py — AODV Mesh Monitor
Listens only on tap port 17550 — never conflicts with any node socket.
Every node copies all forwarded packets here.

Usage:
  python monitor.py
"""

import time
from collections import defaultdict
from pymavlink import mavutil

TAP_PORT = 17550

print("=" * 55)
print(f"  AODV Monitor | Tap port: {TAP_PORT}")
print("=" * 55 + "\n")

mav = mavutil.mavlink_connection(f"udpin:127.0.0.1:{TAP_PORT}")

msg_count   = defaultdict(int)
msg_types   = defaultdict(set)
last_seen   = {}
last_report = time.time()
INTERVAL    = 3.0

print("[MONITOR] Waiting for telemetry on tap...\n")

while True:
    msg = mav.recv_match(blocking=True, timeout=1.0)

    if msg and msg.get_type() not in ("BAD_DATA", None):
        sysid = msg.get_srcSystem()
        mtype = msg.get_type()

        if sysid in (0, 255):
            continue

        msg_count[sysid]  += 1
        last_seen[sysid]   = time.time()
        msg_types[sysid].add(mtype)

    now = time.time()
    if now - last_report >= INTERVAL:
        active = [s for s, t in last_seen.items() if now - t < 6.0]
        lost   = [s for s, t in last_seen.items() if now - t >= 6.0]

        print(f"{'─'*55}")
        print(f"  MESH MONITOR @ {time.strftime('%H:%M:%S')}")
        print(f"{'─'*55}")

        if active:
            print(f"  ✓ ACTIVE : {sorted(active)}")
            for s in sorted(active):
                rate  = msg_count[s] / INTERVAL
                types = ", ".join(sorted(msg_types[s]))
                print(f"    SYSID {s}: ~{rate:.1f} msg/s | {types}")
        else:
            print("  ✗ NO ACTIVE DRONES")

        if lost:
            print(f"  ✗ LOST   : {sorted(lost)}  (silent >6s)")

        print()
        msg_count.clear()
        msg_types.clear()
        last_report = now

"""
disruptor.py — AODV Link Disruptor
Sends DISRUPT or RESTORE control message to any node's ctrl port.
ctrl_port = 15550 + sysid  (auto-derived, no manual config needed)

Usage:
  python disruptor.py --sysid 1                    # kill D1's GCS link
  python disruptor.py --sysid 2                    # kill D2's GCS link
  python disruptor.py --sysid 1,2                  # kill both
  python disruptor.py --sysid 1 --restore          # restore D1
  python disruptor.py --sysid 1 --duration 20      # kill for 20s, auto-restore
"""

import argparse
import socket
import json
import time

parser = argparse.ArgumentParser(description="AODV Link Disruptor")
parser.add_argument("--sysid",    type=str, required=True,
                    help="Comma-separated SYSIDs to disrupt e.g. 1 or 1,2")
parser.add_argument("--restore",  action="store_true",
                    help="Restore GCS link instead of dropping")
parser.add_argument("--duration", type=int, default=None,
                    help="Auto-restore after this many seconds")
args = parser.parse_args()

sysids = [int(s.strip()) for s in args.sysid.split(",")]
action = "restore" if args.restore else "drop"

def send_ctrl(sysid: int, action: str):
    ctrl_port = 15550 + sysid
    msg = json.dumps({
        "type":   "DISRUPT",
        "sysid":  sysid,
        "action": action
    }).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(msg, ("127.0.0.1", ctrl_port))
    s.close()

print("=" * 50)
print(f"  AODV Disruptor")
print(f"  Target SYSIDs : {sysids}")
print(f"  Action        : {action.upper()}")
if args.duration:
    print(f"  Duration      : {args.duration}s then auto-restore")
print("=" * 50 + "\n")

for sysid in sysids:
    ctrl_port = 15550 + sysid
    send_ctrl(sysid, action)
    status = "SEVERED" if action == "drop" else "RESTORED"
    print(f"[DISRUPT] SYSID {sysid} (ctrl:{ctrl_port}) → GCS link {status}")

if action == "drop":
    print(f"\n[*] Watch node terminals — "
          f"SYSID{'s' if len(sysids) > 1 else ''} {sysids} "
          f"will RREQ for a new path now.")

if args.duration and action == "drop":
    print(f"\n[*] Auto-restoring in {args.duration}s...")
    for remaining in range(args.duration, 0, -5):
        time.sleep(min(5, remaining))
        print(f"[DISRUPT] Restoring in {max(0, remaining-5)}s...")
    for sysid in sysids:
        send_ctrl(sysid, "restore")
        print(f"[DISRUPT] SYSID {sysid} → GCS link RESTORED")
    print("\n[*] Done.")

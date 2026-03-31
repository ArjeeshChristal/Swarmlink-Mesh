"""
aodv_node.py — Simplified AODV Mesh Node
All routing is pure UDP port-based. sysid is only used for display/logging.
No HELLO beacons. No neighbor table. No sysid math.

All drones start with a direct GCS link.
When disrupted, RREQ is broadcast to RF band — neighbors reply with RREP.
Best route selected by: highest seq_num first, then lowest hop_count.

Auto-assigned ports (derived from sysid, just for convenience):
  ctrl_port = 15550 + sysid   (JSON: RREQ / RREP / DISRUPT)
  data_port = 16550 + sysid   (raw MAVLink bytes)
  tap_port  = 17550           (monitor tap — all nodes copy here)
  RF band   = 15551–15559     (RREQ broadcast range)

Launch:
  python aodv_node.py --sysid 1 --sitl-port 14551 --gcs-port 14550
  python aodv_node.py --sysid 2 --sitl-port 14561 --gcs-port 14550
  python aodv_node.py --sysid 3 --sitl-port 14571 --gcs-port 14550
"""

import time
import argparse
import socket
import threading
import json
from pymavlink import mavutil

# ── CLI ────────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="AODV Mesh Node")
parser.add_argument("--sysid",     type=int, required=True,
                    help="Display-only drone ID")
parser.add_argument("--sitl-port", type=int, required=True,
                    help="UDP port SITL outputs telemetry to")
parser.add_argument("--gcs-port",  type=int, required=True,
                    help="GCS uplink port (all drones start with direct link)")
args = parser.parse_args()

SYSID     = args.sysid
SITL_PORT = args.sitl_port
GCS_PORT  = args.gcs_port
GCS_SYSID = 255

# ── Auto ports ─────────────────────────────────────────────────────────────────
CTRL_PORT   = 15550 + SYSID
DATA_PORT   = 16550 + SYSID
TAP_PORT    = 17550
BCAST_RANGE = range(15551, 15560)   # RF band — broadcast RREQ to all of these

# ── Tunables ───────────────────────────────────────────────────────────────────
RREQ_COOLDOWN = 5.0    # min seconds between RREQs for same dest
ROUTE_TTL     = 30.0   # drop mesh route after this long (direct routes never expire)

# ── Routing table ──────────────────────────────────────────────────────────────
# { dest_sysid → {data_port, hop_count, seq_num, ts, direct} }
# Routing is purely port-based — sysid in entries is display only
routing_table = {}
routing_lock  = threading.Lock()

# Seed direct GCS route at startup
with routing_lock:
    routing_table[GCS_SYSID] = {
        "data_port": GCS_PORT,
        "hop_count": 1,
        "seq_num":   100,
        "ts":        time.time(),
        "direct":    True,
        "via":       "DIRECT"       # display only
    }

# RREQ flood guard
seen_rreqs  = set()   # (src_ctrl_port, seq_num)
own_seq_num = 0
seq_lock    = threading.Lock()

# Disruption flag
gcs_disrupted = False

# Pending RREPs: { dest_sysid → [rrep, rrep, ...] }
# Collect for 1 second then pick best
pending_rreps = {}
pending_lock  = threading.Lock()

# ── Sockets ────────────────────────────────────────────────────────────────────
ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
ctrl_sock.bind(("127.0.0.1", CTRL_PORT))
ctrl_sock.settimeout(0.1)

data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
data_sock.bind(("127.0.0.1", DATA_PORT))
data_sock.settimeout(0.1)

tap_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

sitl_conn = mavutil.mavlink_connection(f"udpin:127.0.0.1:{SITL_PORT}")

# ── Banner ─────────────────────────────────────────────────────────────────────
print("=" * 58)
print(f"  AODV Node  SYSID:{SYSID}")
print(f"  ctrl:{CTRL_PORT}  data:{DATA_PORT}  tap:{TAP_PORT}")
print(f"  SITL port : {SITL_PORT}  |  GCS port: {GCS_PORT}")
print(f"  RF band   : {BCAST_RANGE.start}–{BCAST_RANGE.stop-1}")
print("=" * 58)
print(f"[TABLE] Seeded: GCS → DIRECT via port {GCS_PORT}\n")

# ── Utilities ──────────────────────────────────────────────────────────────────
def ctrl_broadcast(msg: dict):
    """Simulate RF broadcast — send JSON to every port in RF band except self."""
    data = json.dumps(msg).encode()
    for port in BCAST_RANGE:
        if port != CTRL_PORT:
            try:
                ctrl_sock.sendto(data, ("127.0.0.1", port))
            except Exception:
                pass

def ctrl_send(msg: dict, port: int):
    """Send JSON control message to a specific ctrl port."""
    try:
        ctrl_sock.sendto(json.dumps(msg).encode(), ("127.0.0.1", port))
    except Exception:
        pass

def data_send(raw_bytes: bytes, port: int):
    """Forward raw MAVLink bytes to a data port or GCS port."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(raw_bytes, ("127.0.0.1", port))
        s.close()
    except Exception:
        pass

def tap_send(raw_bytes: bytes):
    """Copy packet to monitor tap — best effort."""
    try:
        tap_sock.sendto(raw_bytes, ("127.0.0.1", TAP_PORT))
    except Exception:
        pass

def next_seq():
    global own_seq_num
    with seq_lock:
        own_seq_num += 1
        return own_seq_num

def print_table():
    with routing_lock:
        print(f"[TABLE] SYSID {SYSID}:")
        if not routing_table:
            print("        (empty — no routes)")
        for dest, e in routing_table.items():
            label = "GCS" if dest == GCS_SYSID else f"SYSID {dest}"
            print(f"        → {label:<6} | data_port={e['data_port']} "
                  f"| hops={e['hop_count']} | seq={e['seq_num']} "
                  f"| via={e['via']}")
    print()

# ── AODV RREQ ─────────────────────────────────────────────────────────────────
def send_rreq(dest_sysid: int):
    seq  = next_seq()
    rreq = {
        "type":           "RREQ",
        "src_sysid":      SYSID,        # display only
        "src_ctrl_port":  CTRL_PORT,    # used for RREP reply
        "src_data_port":  DATA_PORT,    # so replier knows where to send data
        "dest_sysid":     dest_sysid,
        "hop_count":      0,
        "seq_num":        seq
    }
    print(f"[AODV-TX] SYSID {SYSID} → RREQ broadcast "
          f"(dest=GCS seq={seq})")
    ctrl_broadcast(rreq)

def handle_rreq(msg: dict, sender_ctrl_port: int):
    src_sysid  = msg["src_sysid"]       # display only
    src_ctrl   = msg["src_ctrl_port"]   # where to send RREP
    src_data   = msg["src_data_port"]   # where to forward data if we relay
    dest       = msg["dest_sysid"]
    hops       = msg["hop_count"]
    seq        = msg["seq_num"]

    # Flood guard
    uid = (src_ctrl, seq)
    if uid in seen_rreqs:
        return
    seen_rreqs.add(uid)

    print(f"[AODV-RX] SYSID {SYSID} ← RREQ from SYSID {src_sysid} "
          f"(dest=GCS hops={hops})")

    # Do we have a valid route to the destination?
    with routing_lock:
        route = routing_table.get(dest)

    if route and not (dest == GCS_SYSID and gcs_disrupted):
        # We have a route — send RREP back to requester
        rrep = {
            "type":          "RREP",
            "src_sysid":     SYSID,         # display only
            "src_ctrl_port": CTRL_PORT,     # so requester knows who replied
            "src_data_port": DATA_PORT,     # where requester should forward data
            "dest_sysid":    dest,
            "target_ctrl":   src_ctrl,      # who asked — for routing RREP back
            "hop_count":     route["hop_count"] + hops + 1,
            "seq_num":       route["seq_num"]
        }
        print(f"[AODV-TX] SYSID {SYSID} → RREP to SYSID {src_sysid} "
              f"(hops={rrep['hop_count']} seq={rrep['seq_num']})")
        ctrl_send(rrep, src_ctrl)
    else:
        # We don't have a route — flood RREQ onward
        fwd               = dict(msg)
        fwd["hop_count"]  = hops + 1
        fwd["src_ctrl_port"] = CTRL_PORT   # replies come back to us now
        fwd["src_data_port"] = DATA_PORT
        print(f"[AODV-FWD] SYSID {SYSID} flooding RREQ onward (hop {hops+1})")
        ctrl_broadcast(fwd)

# ── AODV RREP ─────────────────────────────────────────────────────────────────
def handle_rrep(msg: dict):
    """
    Collect RREPs for 1 second, then pick the best one:
      1. Highest seq_num  (freshest route)
      2. Lowest hop_count (shortest path) if seq_num ties
    """
    dest = msg["dest_sysid"]
    print(f"[AODV-RX] SYSID {SYSID} ← RREP from SYSID {msg['src_sysid']} "
          f"(hops={msg['hop_count']} seq={msg['seq_num']})")

    with pending_lock:
        if dest not in pending_rreps:
            pending_rreps[dest] = []
            # Schedule best-route selection after 1s collection window
            threading.Timer(1.0, install_best_route, args=[dest]).start()
        pending_rreps[dest].append(msg)

def install_best_route(dest: int):
    with pending_lock:
        candidates = pending_rreps.pop(dest, [])

    if not candidates:
        return

    # Pick best: highest seq_num, break ties with lowest hop_count
    best = max(candidates,
               key=lambda r: (r["seq_num"], -r["hop_count"]))

    via_sysid   = best["src_sysid"]      # display only
    via_data    = best["src_data_port"]  # actual routing
    hops        = best["hop_count"]
    seq         = best["seq_num"]

    with routing_lock:
        ex = routing_table.get(dest)
        if not ex or seq > ex["seq_num"] or hops < ex["hop_count"]:
            routing_table[dest] = {
                "data_port": via_data,
                "hop_count": hops,
                "seq_num":   seq,
                "ts":        time.time(),
                "direct":    False,
                "via":       f"SYSID {via_sysid}"
            }

    print(f"\n[ROUTE] SYSID {SYSID} installed best route to GCS:")
    print(f"        via SYSID {via_sysid} | data_port={via_data} "
          f"| hops={hops} | seq={seq}")
    print(f"        (selected from {len(candidates)} RREP candidate"
          f"{'s' if len(candidates) > 1 else ''})")
    print_table()

# ── DISRUPT handler ────────────────────────────────────────────────────────────
def handle_disrupt(msg: dict):
    global gcs_disrupted
    action = msg.get("action", "drop")

    if action == "drop":
        gcs_disrupted = True
        with routing_lock:
            if GCS_SYSID in routing_table:
                del routing_table[GCS_SYSID]
        print(f"\n{'!'*55}")
        print(f"  [DISRUPT] SYSID {SYSID}: GCS link SEVERED")
        print(f"{'!'*55}\n")
        # Immediately seek a new route
        send_rreq(GCS_SYSID)

    elif action == "restore":
        gcs_disrupted = False
        with routing_lock:
            routing_table[GCS_SYSID] = {
                "data_port": GCS_PORT,
                "hop_count": 1,
                "seq_num":   next_seq(),
                "ts":        time.time(),
                "direct":    True,
                "via":       "DIRECT"
            }
        print(f"\n{'='*55}")
        print(f"  [RESTORE] SYSID {SYSID}: GCS direct link RESTORED")
        print(f"{'='*55}\n")
        print_table()

# ── DATA listener — receives relayed MAVLink from other nodes ──────────────────
def data_listener():
    while True:
        try:
            raw, addr = data_sock.recvfrom(65535)
            with routing_lock:
                route = routing_table.get(GCS_SYSID)
            if route and not (gcs_disrupted and route.get("direct")):
                data_send(raw, route["data_port"])
                tap_send(raw)
                print(f"[RELAY] SYSID {SYSID} relaying {len(raw)}B "
                      f"→ port={route['data_port']}")
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[DATA-ERR] {e}")

threading.Thread(target=data_listener, daemon=True).start()

# ── CTRL listener ──────────────────────────────────────────────────────────────
def ctrl_listener():
    while True:
        try:
            raw, addr = ctrl_sock.recvfrom(4096)
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            mtype = msg.get("type")
            if mtype == "RREQ":
                handle_rreq(msg, addr[1])
            elif mtype == "RREP":
                handle_rrep(msg)
            elif mtype == "DISRUPT":
                handle_disrupt(msg)
        except socket.timeout:
            pass
        except Exception as e:
            print(f"[CTRL-ERR] {e}")

threading.Thread(target=ctrl_listener, daemon=True).start()

# ── Route TTL expiry ───────────────────────────────────────────────────────────
def route_expiry():
    while True:
        time.sleep(10)
        now = time.time()
        with routing_lock:
            stale = [d for d, e in routing_table.items()
                     if not e.get("direct") and now - e["ts"] > ROUTE_TTL]
            for d in stale:
                print(f"[EXPIRE] Route to SYSID {d} TTL expired — removed.")
                del routing_table[d]

threading.Thread(target=route_expiry, daemon=True).start()

# ── Main loop ──────────────────────────────────────────────────────────────────
print(f"[SYS] SYSID {SYSID} online — reading SITL on port {SITL_PORT}\n")
last_rreq_time = {}

while True:
    msg = sitl_conn.recv_match(blocking=False)
    if msg and msg.get_type() not in ("BAD_DATA", None):
        raw   = msg.get_msgbuf()
        mtype = msg.get_type()

        if mtype in ("HEARTBEAT", "GLOBAL_POSITION_INT", "SYS_STATUS"):
            with routing_lock:
                route = routing_table.get(GCS_SYSID)

            if route and not (gcs_disrupted and route.get("direct")):
                data_send(raw, route["data_port"])
                tap_send(raw)
                if mtype == "GLOBAL_POSITION_INT":
                    tag  = "[DIRECT]" if route.get("direct") else f"[MESH via {route['via']}]"
                    print(f"[FWD] {tag} SYSID {SYSID} → port={route['data_port']} "
                          f"({route['hop_count']} hop{'s' if route['hop_count'] > 1 else ''})")
            else:
                now  = time.time()
                last = last_rreq_time.get(GCS_SYSID, 0)
                if now - last > RREQ_COOLDOWN:
                    status = "disrupted" if gcs_disrupted else "no route"
                    print(f"[!] SYSID {SYSID}: GCS {status} — sending RREQ...")
                    send_rreq(GCS_SYSID)
                    last_rreq_time[GCS_SYSID] = now

    time.sleep(0.01)

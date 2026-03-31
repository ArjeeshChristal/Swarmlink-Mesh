# ArduPilot AODV Mesh Routing Prototype (SITL)

This repository contains a decentralized, Python-based testing harness for validating Ad hoc On-Demand Distance Vector (AODV) routing logic for ArduPilot multi-agent swarms. 

By simulating RF broadcast physics using isolated UDP ports, this prototype proves that isolated MAVLink nodes can dynamically discover multi-hop mesh routes when their primary Ground Control Station (GCS) link degrades, preventing the loss of critical telemetry.

This project serves as the architectural proof-of-concept for native integration into ArduPilot's C++ `GCS_MAVLink` library.

## 🏗 System Architecture

The simulation relies on pure UDP port-based routing to mimic decentralized edge-compute nodes. There is no central coordinator.
* **Control Plane (`15550 + SYSID`):** Nodes broadcast JSON-based Route Requests (RREQ) to a simulated RF frequency band (`15551-15559`). Neighbors reply with Route Replies (RREP).
* **Data Plane (`16550 + SYSID`):** Nodes forward raw MAVLink bytes to the optimal next-hop port based on dynamic hop-count and sequence number evaluation.
* **Monitor Tap (`17550`):** A dedicated, collision-free tap port that allows external monitoring of the swarm's health without interfering with node sockets.

## 📂 File Overview

1. `aodv_node.py`: The decentralized mesh daemon. Acts as a standalone state machine for a single drone. Connects to the local SITL physics instance and manages the dynamic routing table.
2. `monitor.py`: A lightweight MAVLink passive listener. Connects to the tap port to display active, degraded, and lost drones in real-time.
3. `disruptor.py`: The "Chaos Monkey" script. Injects a control command to a specific drone to simulate a severed GCS radio link, triggering the AODV mesh failover logic.

## ⚙️ Prerequisites

* Python 3.8+
* `pymavlink` (`pip install pymavlink`)
* [ArduPilot SITL](https://ardupilot.org/dev/docs/sitl-simulator-software-in-the-loop.html) (Software In The Loop)

---

## 🚀 Quickstart: Running the 3-Node Mesh Simulation

To fully observe the decentralized network forming and healing, you will need to open multiple terminal windows.

### Step 1: Launch the ArduPilot Physics Bodies (SITL)
Open 3 separate terminals and start the simulated Copter instances. Notice they output to unique SITL ports.
```bash
# Terminal 1
sim_vehicle.py -v ArduCopter -I 0 --sysid 1 --out 127.0.0.1:14551

# Terminal 2
sim_vehicle.py -v ArduCopter -I 1 --sysid 2 --out 127.0.0.1:14561

# Terminal 3
sim_vehicle.py -v ArduCopter -I 2 --sysid 3 --out 127.0.0.1:14571
```
### Step 2: Launch the Swarm Monitor

Open a 4th terminal to watch the swarm's telemetry flow.

```bash
# Terminal 4
python monitor.py
```
### Step 3: Start the Decentralized AODV Daemons

Open 3 more terminals. These daemons attach to their respective SITL bodies and establish direct links to the GCS (Port 14550).

```bash
# Terminal 5 (Node 1)
python aodv_node.py --sysid 1 --sitl-port 14551 --gcs-port 14550

# Terminal 6 (Node 2)
python aodv_node.py --sysid 2 --sitl-port 14561 --gcs-port 14550

# Terminal 7 (Node 3)
python aodv_node.py --sysid 3 --sitl-port 14571 --gcs-port 14550
```
At this point, check your monitor.py terminal. It should show SYSID 1, 2, and 3 actively connected.
### Step 4: Trigger Link Degradation & Mesh Failover

Open a final terminal to sever Node 1's connection to the GCS.

```bash
# Terminal 8
python disruptor.py --sysid 1 --duration 20
```
Things to watch for:

    In Terminal 5 (Node 1), you will see [DISRUPT] GCS link SEVERED.

    Node 1 will broadcast an RREQ.

    Nodes 2 and 3 will reply with an RREP.

    Node 1 will install the best route and begin forwarding [MESH via SYSID 2] or SYSID 3.

    The Monitor will seamlessly continue to show Node 1 as active, proving the mesh successfully bypassed the link loss.

🛠 Port Mapping Reference

If you wish to scale the simulation beyond 3 drones, the scripts automatically calculate network ports using the drone's SYSID:
Function	Port Calculation	Example (SYSID 2)
GCS Telemetry	User Defined	14550
SITL Output	User Defined	14561
AODV Control (JSON)	15550 + SYSID	15552
AODV Data (MAVLink)	16550 + SYSID	16552
RF Broadcast Band	Fixed Array	15551 - 15559
Monitor Tap	Fixed	17550

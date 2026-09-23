# The machine

What the hardware physically does, and which axis moves which mechanism.

[`architecture.md`](./architecture.md) is the *software* component map —
renderer, host, PLC, the events between them. This is the layer under
it: the metal. Read this first if you have never stood in front of the
machine, then read architecture.md to see what drives it.

For the bus-level configuration (device ids, DC settings, scaling,
IO mapping) see [`../3-subsystems/ethercat_config.md`](../3-subsystems/ethercat_config.md).

> **Provenance.** Layout and mechanism here were read off photographs of
> the rig plus the device tree in `PackerX.project`; names in `code font`
> are verified against the project or the PLC's own reports. Anything
> described in plain prose about the physical machine is an inference
> from the photos and worth correcting if wrong.

---

## What it does

It is a **taping machine** — hence `PackerX`. Small metal components
arrive loose, and leave seated one-per-pocket in sealed carrier tape on
a reel.

A bowl feeder vibrates parts onto a flat bulk tray where they land in
arbitrary positions. A delta robot picks them off that tray with a
vacuum nozzle, vision decides whether the part is good and how it is
oriented, and the robot places it into the next pocket of the carrier
tape running underneath. Bad parts go to NG bins instead. The tape
advances, cover tape seals the pockets, and the reel winds the finished
strip.

![Material flow through the machine](./machine-flow.svg)

The same flow as text, for terminals and diffs:

```
  bowl feeder ──feeds──▶ bulk tray ──pick──▶ delta nozzle ──┬──▶ tape pocket ──▶ reel
  Modbus addr 1          loose parts         EAxis0/1/2     │    ConveyorEncoderAxis
                                                  ▲         │    reelpullmotor
                                                  │         └──▶ NG bins
                                             vision says
                                             good / bad / pose
```

---

## Stations, lighting and the per-part cycle

The station layout, lighting (lights 1-5), cameras, the per-part cycle,
the tape slot rules and the two timing-critical paths are in
[`plc.md`](../3-subsystems/plc.md#machine-sequence--parallelism-constraint),
next to the timing diagram they belong with.

---

## Delta arm orientation

Three arms at 120°. `arm2` points along the carrier tape; `arm0` and
`arm1` trail behind it. The motors carry handwritten `AX0` / `AX2`
labels on the rig.

```
          arm1
            ╲
             ╲
              ╭──────╮
              │ base │────── arm2 ─────▶   direction of tape travel
              ╰──────╯
             ╱
            ╱
          arm0
```

The PLC reports these as `axes_labels = ["EAxis0 (arm1)", "EAxis1
(arm2)", "EAxis2 (arm3)", "reelpullmotor"]`. **Note the off-by-one
between the two namings**: the project's `EAxis0` carries the label
`arm1`. When someone says "arm 2", establish which of the two schemes
they mean before touching anything.

---

## Axis map

| Axis | DriveID | Moves | Notes |
|---|---|---|---|
| `EAxis0` | 3 | Delta arm | `bVirtual` pinned true in the device tree |
| `EAxis1` | 4 | Delta arm | `bVirtual` pinned true |
| `EAxis2` | 5 | Delta arm | `bVirtual` pinned true |
| `EAXIS_A` | 6 | QEC drive axis | not virtual |
| `SM_Drive_GenericDSP402` | 7 | QEC drive axis | `bVirtual` pinned true |
| `reelpullmotor` | 8 | Carrier tape advance / reel | modulo axis, period 200; direction inverted twice |
| `ConveyorEncoderAxis` | — | Belt position tracking (virtual) | feeds `Coord1CommitBind` |

The delta trio being virtual is the **tape-binding bench mode** added
2026-07-08: the reel motor runs for real while the arms are simulated,
so tape feeding can be commissioned without the robot moving. That is
why the PLC currently reports `axes_sim_mask = 7`.

---

## Control topology

```
  ┌──────────────┐   msgpack TCP        ┌────────────────────────────────┐
  │  Host PC     │──────────────────────▶│  SCIPC                         │
  │  Electron UI │   192.168.1.70:8125   │  Intewell + CODESYS runtime    │
  └──────────────┘                       │  target id 180A 0002           │
         │                               │                                │
         │ USB                           │  EtherCAT master   1 ms        │──▶ servos, IO
         ▼                               │    adapter I210_2              │
   HIKROBOT cameras                      │                                │
   VisionMaster                          │  Modbus RTU  COM3              │──▶ RS-485
                                         │    19200 8N1, addr 1           │    bowl feeder
                                         └────────────────────────────────┘
```

Two independent buses: **EtherCAT** for everything that has to be
deterministic (the servos, the digital IO coupler), and **Modbus RTU
over RS-485** for the feeder, which only needs setpoints and status.

---

## The feeder problem

A flexible feeder is to be added, and it speaks RS-485. Today it would
be driven from the PC over a USB-485 adapter, which splits control
across two machines:

```
  current                                proposed
  ───────                                ────────
  PC ──USB-485──▶ flexible feeder        PC ──TCP──▶ PLC ──RS-485──┬──▶ bowl feeder      addr 1
  PC ──TCP──────▶ PLC                                              └──▶ flexible feeder  addr 2
                   └──RS-485──▶ bowl feeder
```

The PC path works but leaves the feeder outside the PLC's timing and
state machine: the two have to be kept in step by the host, and
production depends on the PC being up. Putting both feeders on the
PLC's existing RS-485 bus at different `ServerAddress` values removes
that seam.

**Blocked on:** the PLC's RS-485 has never been made to work. Current
configuration and the candidate causes are in
[`../3-subsystems/ethercat_config.md`](../3-subsystems/ethercat_config.md#rs-485--modbus-rtu)
— in short, `ComPort = 3` may not map to the physical port, and the
slave's channel list may be empty. Dump the live configuration with:

```bash
python codesys_scripts/rpc.py exec --readonly \
    --file codesys_scripts/jobs/templates/dump_serial.py
```

---

## Peripherals not on either bus

| Thing | How it is driven |
|---|---|
| HIKROBOT cameras | USB/GigE to the PC, through VisionMaster |
| Samkoon HMI | its own panel, alongside the CODESYS application |
| Cover tape seal, tape guides | mechanical / pneumatic |

The vacuum nozzle and the pneumatics are switched through the EtherCAT
digital outputs — `AxisGroupSM.OutputCHs[0..6]` on the `EC0808DN`
coupler and its two terminal modules.

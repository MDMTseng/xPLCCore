# EtherCAT bus configuration

The hardware layer as configured in `PackerX.project`: master settings,
slave topology, per-axis scaling, and every bound IO channel.

This lives in the `.project` file and nowhere else. It is not in the
`.st` tree, so it is not in git and does not survive losing the project
— which is exactly what nearly happened during the 2026-09 machine
migration, when 9 of the project's device descriptions turned out not to
be installed on the new machine and the project was silently unbuildable
while still opening and precompiling cleanly. Treat this document as the
written record of what the bus is supposed to look like.

Complements [`softmotion_snapshot.yaml`](./softmotion_snapshot.yaml),
which covers the axis pool and `AxisGroupManager` declarations but
carries no device parameters (`attrs: {}`).

**Regenerate with:**

```bash
python codesys_scripts/rpc.py exec --readonly \
    --file codesys_scripts/jobs/templates/dump_ethercat.py
```

**Verify the descriptions still resolve on this machine:**

```bash
python codesys_scripts/rpc.py exec --readonly \
    --file codesys_scripts/jobs/templates/check_devices.py
```

Captured 2026-09-23 from `NewPrj/PackerX.project` on CODESYS 3.5.22.30.

---

## Reading the parameter dump

`dump_ethercat.py` prints only parameters whose value differs from the
device description's default — EC0808DN alone carries 294, and printing
all of them buries the handful of decisions anyone actually made.

The comparison is **textual**, so a value stored as `1` against a
default of `TRUE` is reported as changed when it is the same setting
written two ways. In the tables below those have been filtered out.

---

## Master

`EtherCAT_Master_SoftMotion` — device id `(64, 0000 1002, 4.9.0.0)`

| Parameter | Value | Default | Note |
|---|---|---|---|
| `MasterCycleTime` | **1000 µs** | 4000 | 1 ms bus cycle |
| `NetworkName` | `I210_2` | — | The **PLC's** adapter, not the dev PC's |
| `SyncOffset` | 0 | 20 | |
| `SyncWindowMonitoring` | **1000** | 0 | Enabled (0 = off) |
| `SrcAddress1` / `SrcAddress2` | 21076 / 6159774 | 0 | Adapter MAC |
| `EnableSecondAdapter` | False | FALSE | Unchanged |

`NetworkName` names an interface on the SCIPC controller. It is
unaffected by moving the CODESYS installation between PCs, but it will
break if the PLC's own NIC is replaced or renamed.

---

## Topology

```
EtherCAT_Master_SoftMotion              1 ms
├─ EC0808DN                D37   IO coupler, RxPDO 9 / TxPDO 5
│  ├─ Terminals            D37   16-point DO/DI module
│  ├─ Terminals_1          D37   32-point DO module
│  └─ Terminals_2 .. _15         empty slots (14)
├─ ASDA_B3_E_CoE_Drive     1DD   → EAxis0
├─ ASDA_B3_E_CoE_Drive_1   1DD   → EAxis1
├─ QEC_R11MP3S_V           BC3   RxPDO 3 / TxPDO 3
│  ├─ Axis_1 / Axis_2 / Axis_3   modules, no non-default parameters
│  ├─ EAXIS_A
│  └─ SM_Drive_GenericDSP402
├─ ASDA_B3_E_CoE_Drive_2   1DD   → EAxis2
└─ reel_pull_motor         A79   → reelpullmotor
```

32 device nodes in total: 18 real devices plus the 14 empty module slots,
which report as `(0, 0000 0000, 3.0.0.0)` and are not missing drivers.

**Vendors** (the numeric ids are what the project stores; the names come
from the device descriptions):

| Vendor id | Who | Devices here |
|---|---|---|
| `180A` | Intewell Guangzhou Software Technology | SCIPC (the PLC target) |
| `1DD` | Delta Electronics | ASDA-B3-E drives ×3 |
| `D37` | — | EC0808DN coupler, C1616DN, C0032DN |
| `BC3` | — | QEC-R11MP3S_V and its axis modules |
| `A79` | — | CL3-E57H (the reel pull motor) |

### Distributed clocks

Identical across every slave that has DC parameters:

| Parameter | Value |
|---|---|
| `DC sync0 cycletime` | 1000 |
| `DC sync1 cycletime` | 1000 |
| `DC sync0 enable` | 1 |
| `DC sync1 enable` | 0 |

`reel_pull_motor` sets the two cycletimes but leaves the enables at
their description defaults.

---

## Axes

Six drives, drive IDs 3–8. All use `eRampType = 0` (default 2).

| Axis | DriveID | `bVirtual` | Ratio num/denom | ScalingIncs | ScalingUnits | MaxVel | MaxAcc | MaxJerk |
|---|---|---|---|---|---|---|---|---|
| `EAxis0` | 3 | **TRUE** | 45 / 65011712 | `16#1000000` | 360 | 100 000 | 800 000 | 10 000 000 |
| `EAxis1` | 4 | **TRUE** | 45 / 65011712 | `16#1000000` | 360 | 100 000 | 800 000 | 10 000 000 |
| `EAxis2` | 5 | **TRUE** | 45 / 65011712 | `16#1000000` | 360 | 100 000 | 800 000 | 10 000 000 |
| `EAXIS_A` | 6 | — | 9 / 1600 | — | — | 1 800 | 20 000 | — |
| `SM_Drive_GenericDSP402` | 7 | **TRUE** | 9 / 1600 | 6400 | 36 | 180 000 | 200 000 | 2 000 000 |
| `reelpullmotor` | 8 | — | **−1** / 256 | 51200 | 200 | 5 000 | 100 000 | 100 000 |

The delta trio also sets `ScalingMotorTurns2 = 31`: 16 777 216 increments
per 31 motor turns per 360 units, i.e. a **31:1 reduction**, consistent
with the gear ratio fields.

`reelpullmotor` is a modulo axis — `iMovementType = 0`,
`fPositionPeriod = 200` — and sets `InvertDirection = TRUE`.

---

## IO mapping

Eleven bound channels. Everything else on the bus is driven through
SoftMotion rather than direct IO.

| Device | Channel | Dir | Width | Variable |
|---|---|---|---|---|
| `EC0808DN` | Digital output CH1 | Out | 8 bit | `Application.AxisGroupSM.OutputCHs[0]` |
| `EC0808DN` | Digital input CH1 | In | 8 bit | `Application.AxisGroupSM.InputCHs[0]` |
| `Terminals` | Digital output CH1 | Out | 8 bit | `…OutputCHs[1]` |
| `Terminals` | Digital output CH2 | Out | 8 bit | `…OutputCHs[2]` |
| `Terminals` | Digital input CH1 | In | 8 bit | `…InputCHs[1]` |
| `Terminals` | Digital input CH2 | In | 8 bit | `…InputCHs[2]` |
| `Terminals_1` | Digital output CH1–CH4 | Out | 8 bit each | `…OutputCHs[3]` … `[6]` |
| `ASDA_B3_E_CoE_Drive_1` | Target Position | Out | 32 bit | `biSetPos` |

---

## RS-485 / Modbus RTU

Not on the EtherCAT bus, so `dump_ethercat.py` does not see it. Use:

```bash
python codesys_scripts/rpc.py exec --readonly \
    --file codesys_scripts/jobs/templates/dump_serial.py
```

```
Modbus_COM               (92, 0000 0001, 4.4.0.0)
  ComPort              = 3          on the PLC, not the dev PC
  Baudrate             = 19200      non-default
  Parity / Data / Stop = NONE / 8 / 1

Modbus_Client_COM_Port   (90, 0000 0002, 4.4.0.0)
  Transmission         = RTU
  ResponseTimeout      = 1000 ms
  TimeBetweenFrames    = 10 ms
  Auto-restart         = TRUE       non-default

SmartBowlFeeder          (91, 0000 0001, 4.4.0.0)
  ServerAddress        = 1
  ResponseTimeout      = 1000 ms
```

All three nodes are enabled.

### Status: not working -- parked (2026-09-23)

The bus has never been made to function, which is why the flexible
feeder is currently driven from the PC over a USB-485 adapter instead
(see [`../1-concepts/machine.md`](../1-concepts/machine.md#the-feeder-problem)).

**1. No bus cycle task (fixed).** The PLC-level bus cycle task
(Device -> PLC Settings) pointed at task guid `d109705d-...`, which is
none of `Comm` / `EtherCAT_Task` / `SoftMotion_PlanningTask` -- a dangling
reference, probably from a task deleted and recreated. Every Modbus node
inherited it. `set_modbus_bus_cycle.py` pins `Modbus_COM` and
`Modbus_Client_COM_Port` to `Comm` explicitly; the PLC-level setting is
left alone so the EtherCAT master does not move. Inspect with
`probe_bus_cycle.py`.

**2. SmartBowlFeeder had no channels (fixed).** `PRG_MbProbe`
(`create_mb_probe.py`, Comm task) runs `IoDrvModbus.ModbusChannel`
through the master's own port; channels 0-2 all returned `xError` with
`ModbusError = UNDEFINED` (index does not exist), and the device had no
I/O-mapping parameters. With no channels the master has nothing to send.

Channels are not in the scripting API. `create_mb_channels.py` adds them
by driving the device editor plugin (`DeviceEditorModbus.plugin`,
internal types, via reflection) the same way the "Add Channel" button
does. Now configured:

```
rs485_cfg   FC03  read 0x0039 len 1  cyclic 1000 ms  -> mb_rs485_cfg[0]
            (feeder's RS-485 baud/address; 0x0501 = 1281 expected)
```

The feeder's register map is in [`flex_feeder.md`](./flex_feeder.md).

**3. `ComPort = 3` is probably not the RS-485 terminal (open).**
Every request fails with `RESPONSE_CRC_FAIL`, and the failure does not
depend on the bus:

| Test | Result |
|---|---|
| Feeder connected, address 1 | `RESPONSE_CRC_FAIL` |
| Address changed to 99 (nobody answers) | `RESPONSE_CRC_FAIL` |
| Feeder unplugged | `RESPONSE_CRC_FAIL` (fresh on every run, not latched) |
| Both wires removed from the PLC terminal | `RESPONSE_CRC_FAIL` |

With nothing attached, bytes still come back, so they are produced on
the PLC side: either COM3 is some other port that talks back, or the
port reads its own transmission (RS-485 echo). The history points at
the first: `ComPort = 3` matches the PC-side USB-485 adapter
(`PluginHello.tsx` defaults to `'COM3'`), and the machine's previous PLC
had no RS-485 at all. CODESYS COM numbers belong to the PLC runtime and
have nothing to do with the PC's. The SCIPC runtime does not expose
`CODESYSControl.cfg` (`plc_fetch_cfg.py` finds no `[SysCom]`), so the
mapping cannot be read. The COM port device has no RS-485 mode or echo
option.

**Next:** with the feeder wired (PLC A = red, B = blue), scan `ComPort`
with `set_mb_comport.py` (edit `COMPORT`), then `download_start_virtual.py`
and fire `PRG_MbProbe`:

```bash
python codesys_scripts/rpc.py write PRG_MbProbe.iChannel 0
python codesys_scripts/rpc.py write PRG_MbProbe.xRun TRUE
python codesys_scripts/rpc.py read  PRG_MbProbe.eErr
python codesys_scripts/rpc.py read  "mb_rs485_cfg[0]"
```

| Result | Conclusion |
|---|---|
| `xDone`, `mb_rs485_cfg[0]` = 1281 | Right port: link, baud and address all good |
| `RESPONSE_TIMEOUT` | A real port, not the one wired to the feeder |
| `RESPONSE_CRC_FAIL` | Something else that talks back, like COM3 |
| Port open error | That number does not exist |

If no number works, ask the vendor (Intewell) which CODESYS COM number
the A/B/GND terminal is, and whether RS-485 needs a jumper or a runtime
setting for half-duplex (echo suppression). `set_mb_server_address.py`
changes the slave address for the address-99 style test.

Notes for whoever picks this up:

- `SmartBowlFeeder.xError` latches; `xAcknowledge` clears it. Prefer
  `PRG_MbProbe`, whose counters prove each result is fresh.
- Online reads of the Modbus diagnostics once stalled the daemon for
  minutes; a daemon restart cleared it.

### With a USB-485 listener

Bridge the existing USB-485 adapter onto the bus as a listener (A/B in
parallel, no extra termination) and watch at 19200 8N1:

| On the wire | Conclusion |
|---|---|
| Nothing at all | The PLC is not transmitting -- no channels, or the COM mapping |
| Requests, no replies | Port is right; wiring, A/B polarity, termination or feeder settings |
| Requests and replies, wrong values | Link is fine; register map or data format |

One measurement eliminates half the list.

---

## Things worth knowing

**`bVirtual` is pinned in the device tree, not just at runtime.**
EAxis0/1/2 and `SM_Drive_GenericDSP402` (drive 7) have `bVirtual = TRUE`
stored in the project. `EAXIS_A` (drive 6) and `reelpullmotor` (drive 8)
do not.

This is the boot-time half of the per-axis simulation feature added
2026-07-08. `GVL.AxisSimMaskApplied` is derived live from the drives'
own `bVirtual` flags each scan, so the device-tree checkboxes are what
the PLC reports as `axes_sim_mask` before any host request — currently
`7`, i.e. the delta trio simulated and the reel pull motor real. See the
`AxisSimMask` block in [`../../codesys_code/Application/GVL.st`](../../codesys_code/Application/GVL.st).

**The reel pull motor is inverted twice.** `iRatioTechUnitsNum = -1` and
`InvertDirection = TRUE` are both sign flips. They may be intended to
cancel, but if axis direction has ever been confusing, start here.

**One stray IO mapping.** `ASDA_B3_E_CoE_Drive_1` binds `Target Position`
straight to `biSetPos` at the EtherCAT level. The other two ASDA drives
have no such binding, and the axis is already under SoftMotion control —
this looks like debug residue rather than a deliberate part of the
design. Worth confirming before the next download.

**Device descriptions are not in git.** They live in
`C:\ProgramData\CODESYS\Devices`. To move this project to another
machine, export a project archive with *Device descriptions* and
*Library files* ticked — see [`../4-dev/scripting.md`](../4-dev/scripting.md)
— and run `check_devices.py` afterwards to confirm every node resolves.

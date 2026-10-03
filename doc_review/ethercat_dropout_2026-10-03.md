# EtherCAT dropouts with the QEC and reel resetting (2026-10-03)

Status: **open, hardware**. Since the afternoon of 2026-10-03 the bus
loses all frames for ~2.5 s now and then: the servos lose DC sync and go
to errorstop if they are enabled, and the QEC (station 1002) and the reel
servo (1003) come back in INIT / SAFE-OP, as if they had rebooted. It
happens with the delta moving or standing still. Software is ruled out;
the next step is on the machine (power of the QEC and the reel).

## 1. Symptom

PLC log at each dropout (PLC clock ~4 min ahead of the PC):
- `Fieldbus lost synchronicity` on every SoftMotion drive, then
  `Error 11005: An axis of the axis group is in state error`;
- `Frames Lost/Tx err/Rx err: 43/0/44` (or 16/0/17) and `DC phaseDist min
  -43006 us` (or -10996, -15993): no frame came back for 11-43 ms;
- then `more than 100 packets lost, perhaps communication lost ! check the
  cables !`; the master's lost-frame counter (SYS EC_STATS `lost`) ends
  ~2200-2600 higher, i.e. ~2.5 s without frames;
- `ETC device is no longer in mode operational`.

Task jitter stays normal (+-35..44 us): the PLC keeps cycling.

Slave states afterwards (`IoConfig_Globals.<slave>.wState`):

| When | EC0808DN | QEC | reel | EAxis0 | EAxis1 | EAxis2 | EasyCAT |
|---|---|---|---|---|---|---|---|
| 13:06 (dip 70 %, moving) | OP | 0 | 0 | SAFE-OP | 0 | 0 | OP |
| 17:1x (standstill) | ? | SAFE-OP | INIT | OP | OP | OP | OP |
| 17:47 (standstill) | OP | 0 | 0 | OP | OP | OP | OP |

The QEC and the reel drop every time; the ASDA drives drop only when they
were enabled (DC sync loss). ~2.5 s without frames is the time a slave
needs to reboot and bring its link back, not a cable glitch (ms).

The ASDA alarm history (P4.000) shows only AL180 (EtherCAT heartbeat,
0x8130) on all three: a consequence. QEC 0x603F = 0. The reel's error
object could not be read (not in OP).

## 2. Runs

All 2026-10-03, after the slave reorder of 2026-10-02 (EC0808DN first on
the wire, tree = wire, no slave optional; doc_review/asda_stale_target
7l).

| Start | Run (`tools/circle_soak.py`) | PLC program | Result |
|---|---|---|---|
| 07:47 | round 30 %, 60 min | before DWELL | clean |
| 11:31 | square dip 70 %, 60 min | before DWELL | clean |
| 12:35 | dip 70 % | before DWELL | stopped by hand at 9 min |
| 12:51 | dip 70 % + dwell CSV | DWELL | **dropout at 14.9 min** |
| 13:32 | dip 70 % + dwell CSV | DWELL | **dropout at 14.9 min** |
| 13:52 | dip 70 %, `--virtual`, 20 min | DWELL | clean |
| 14:18 | dip 30 % + dwell CSV | DWELL | **dropout at 30.4 min** |
| 15:01 | dip 30 % | **before DWELL** | **dropout at 2.9 min** |
| 15:26 | Z 0 <-> -20 at 10 %, 60 min | DWELL | clean |
| 16:32 | Z 0 <-> -20 at 10 % (6 h planned) | DWELL | **dropout at 4 min** |
| 17:1x | standstill, delta virtual and off | DWELL | **dropout** |
| 17:47 | standstill (`tools/bus_watch.py`) | DWELL | **dropout at 0.5 min** |

Ruled out:
- the every-stop log (SYS DWELL) and the CSV readout: the program without
  it dropped too (15:01);
- motion and load: the bus drops at standstill;
- the detached UI / harness: the virtual run used them and stayed clean.

Getting more frequent through the afternoon. Not yet ruled out: a
supply shared by the QEC and the reel, their wiring, either device
itself, the EC0808 -> QEC cable re-plugged on 2026-10-02.

## 3. What cannot be read

The CODESYS EtherCAT libraries (EtherCATStack 4.9, IODrvEtherCATDriver)
offer SDO / FoE / SoE / VoE and master statistics only: no access to a
slave's ESC registers, so the per-port RX error and lost-link counters
(0x0300-0x0313) that would name the failing link cannot be read from the
PLC. A separate EtherCAT diagnostic tool (e.g. TwinCAT) on the bus in
place of the PLC could read them; a power cycle of the slaves clears
them. The owner has no such tool.

## 4. Next steps (on the machine)

1. The QEC's and the reel driver's power: one shared supply? Terminals,
   voltage, temperature; do their LEDs show a reboot now and then?
2. Power the reel driver (or the QEC) from a separate supply, or bypass
   one of them (`jobs/templates/set_slaves_bypass.py`), and watch with
   `tools/bus_watch.py`.
3. Replace the EC0808DN -> QEC cable.

## 5. Tools

- `tools/circle_soak.py`: long soak (`--shape round|dip|zud`, `--speed`,
  `--minutes`, `--virtual`, `--dwell-log CSV`); per minute late %, torque
  change, EtherCAT lost frames and the every-stop summary; stops on a
  dropout and puts the delta back to virtual.
- `tools/soak_segments.py`: soaks over 2 h in 60 min segments,
  `--hours H --recover N` re-downloads after a dropout and goes on. Run it
  detached (PowerShell `Start-Process`), output in
  `codesys_scripts/jobs/soak_logs/`.
- `tools/bus_watch.py`: standstill watch, every 10 s; on a dropout logs the
  slave states and re-downloads.
- PLC SYS DWELL (GVL.Dw*): every stop of the delta, 10-15 ms PnP dwells
  too: dwell, settle time to 0.2 mm, error at the stop and when leaving.
  Square dip 70 %: errors at the stop ~23 um, when leaving ~5 um, all
  within 0.2 mm before leaving.

After a dropout: the delta is virtual and off (circle_soak does it,
`DELTA_MODE real:0` when the axes sit in errorstop), the bus needs a
re-download (`tools/safe_install.py`); SDO to the slaves fails (error 4)
until then. After a machine power cycle the USB-powered ESP32 of the
EasyCAT must be reset too, then a re-download.

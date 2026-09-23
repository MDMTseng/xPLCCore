# EasyCAT PRO + ESP32 -- EtherCAT slave

An ESP32 behind an AB&T EasyCAT PRO (LAN9252) as a custom EtherCAT slave,
synchronised to the bus by distributed clocks (DC). Standard 32+32 byte
process image: vendor `0x079A` AB&T, product `0x00DEFEDE`, **revision
`0x5A01`** "EasyCAT 32+32 rev 1", ESI `esi/EasyCAT_V2_0.xml`.

## Distributed clocks

The master drives SYNC0 every 1000 us on this slave exactly as on the
servo drives (CODESYS: DC enable, SYNC0 enable, `DCSetting = 1` =
"DC_Sync" opmode, AssignActivate `#x300`). The LAN9252 maps SYNC0 to its
AL event and raises INT (GPIO17); the ISR wakes a high-priority task that
samples the encoder and runs `MainTask()`. Measured: 1 kHz interrupts,
task interval 939-1061 us.

Three things have to agree, and two of them were wrong at first:

| Piece | Needed | Notes |
|---|---|---|
| EEPROM config, ESC `0x0151` | `0x6E` (SYNC0 output, SYNC0 -> AL event) | Already on this board. The firmware prints it at boot (`dumpEscConfig`), so no EEPROM rewrite was needed |
| ESI in CODESYS | rev `0x5A01` with `<Dc>` | `EasyCAT_V2_0.xml` from EasyConfigurator. The website's `EasyCAT_PRO.xml` is rev `0x5A00` **without** DC -- installing that one is why DC was not offered at first |
| Firmware | `EasyCAT(PIN_SCS, DC_SYNC)` | |

Per-cycle work must stay well under 1 ms or SYNC0s are skipped: with
three I2C reads at 100 kHz the task ran every 2 ms. Now I2C runs at
400 kHz, the angle is read every cycle and STATUS/AGC every 64th.

Input bytes 11-14 report the max/min task interval over the last second
(us) and byte 15 is 1 while cycles are driven by SYNC0 (0 = polling
fallback, e.g. before the master starts DC).

**Unplugging the EasyCAT stops the whole bus.** It is identified by
position (no station alias in its EEPROM) and not optional, so a missing
EasyCAT fails the master's startup check ("more slaves in config as
real?") and every axis stays down. Reflashing the ESP32 also resets the
LAN9252; restart EtherCAT afterwards. Making it optional needs a station
alias written into the EEPROM (EasyConfigurator, Extra -> Alias address,
with the board cabled straight to a PC).

## Wiring

| EasyCAT PRO revB | ESP32 | Signal |
|---|---|---|
| 3.3 | 3V3 | Power -- **never 5V/VIN** |
| G | GND | Ground |
| MI | GPIO19 | VSPI MISO |
| MO | GPIO23 | VSPI MOSI |
| SCK | GPIO18 | VSPI SCK |
| SCS | GPIO5 | Chip select |
| INT | GPIO17 | Interrupt: SYNC0 via the AL event |
| SYN0 | GPIO16 | DC SYNC0 pin (unused; SYNC0 arrives on INT) |
| SYN1 | GPIO4 | DC SYNC1 (unused) |

Unplug USB before rewiring: hot-plugging wires once left the CP210x
returning "device not functioning" until the USB cable was replugged.

## Build and flash

```bash
python -m pip install --user platformio
cd firmware/easycat_esp32
python -m platformio run -t upload        # COM3 in platformio.ini
```

Serial monitor at 115200. A good bring-up looks like:

```
Detected chip 9252  Rev 1
Init OK: LAN9252 answered on SPI (byte test 0x87654321)
ESC state: INIT (0x81)
```

`Init FAILED  BYTE_TEST=0x...` names the likely fault: `0x00000000` is
MISO stuck low (nothing connected, MI/MO swapped), `0xFFFFFFFF` is MISO
floating high (no power, SCS wrong), anything else is a loose SCK/MOSI.

The first ESP32 tried always booted into download mode (`boot:0x3`,
GPIO0 low) even with nothing attached and no serial port open -- a board
fault. A healthy boot prints `boot:0x13 (SPI_FAST_FLASH_BOOT)`.

## Encoder

QY2204-IIC (ACCNT) magnetic absolute encoder, AS5600 inside, I2C `0x36`,
12-bit. The 3.3/5 V variant (`5E`) runs from 3V3.

| Encoder (XH2.54) | ESP32 |
|---|---|
| Pin 1 GND | GND |
| Pin 2 VCC | 3V3 |
| Pin 3 OUT | -- (analog/PWM, unused) |
| Pin 4 SCL | GPIO22 |
| Pin 5 SDA | GPIO21 |
| Pin 6 NC | -- |

The AS5600's CONF register is set at every boot (volatile, nothing is
burned to OTP): slow filter 16x, fast filter above 6 LSB so real motion
is not lagged, 2 LSB hysteresis. The firmware reads ANGLE (0x0E), which
the hysteresis applies to, instead of RAW ANGLE. At rest the reading
used to flicker 864/865 and the multi-turn position 0/1; now it holds.

**Pin 1 is on the latch side** of the XH connector (the vendor manual:
"左边倒钩起为 Pin1"). Counted from the other end the encoder gets no power
and the boot scan finds nothing on either SDA/SCL order.

## Stepper (open-loop CSP)

For rotating a picked part. STEP/DIR/EN to any step-direction driver.
Pulses come from hardware (RMT), and the STEP pin is counted back by
hardware (PCNT) -- see "Hardware pulses" below.

| Driver | ESP32 |
|---|---|
| STEP / PUL | GPIO25 |
| DIR | GPIO26 |
| EN / ENA | GPIO27 (active low by default, `EN_ACTIVE_LOW` in `stepper.h`) |
| GND / COM | GND |

Signals are 3.3 V. Opto-isolated drivers that want 5 V on their inputs
need a level shifter or a common-anode wiring that works at 3.3 V.

The PLC (`PRG_EcatStepper`, EtherCAT_Task) plans the move and sends an
absolute step target every 1 ms; the ESP32 only interpolates. No stepper
library on purpose: AccelStepper-style libraries plan their own ramps,
which would fight the PLC's.

### Hardware pulses

- **RMT** generates them. Each cycle the move (`target - scheduled`)
  becomes N RMT items spread evenly over 900 us at 0.1 us resolution;
  the peripheral plays them with no CPU involvement. Pulse 3 us high,
  first pulse 5 us into the sequence (DIR setup), at most 150 steps per
  cycle (150 kHz). The PLC caps itself at 135.
- **PCNT** counts them: the STEP pin's input buffer is routed back to
  pulse counter 0 with DIR as direction control. The reported actual
  position is what the hardware emitted, not what software intended, and
  it stays exact when a sequence is aborted.
- DIR only flips after the previous sequence has finished.
- The first version was a 100 kHz timer interrupt stepping a DDA:
  100 000 interrupts per second, a 10 us timing grid, 50 kHz ceiling.

Routing order matters because the IDF drivers undo each other: PCNT is
configured without pins, RMT then claims STEP as output, and only then are
the STEP/DIR input buffers enabled and connected to PCNT.

### CPU and timing (measured)

| | |
|---|---|
| EtherCAT task per cycle (core 1) | 421-495 us |
| Encoder task per sample (core 0) | ~700 us |
| SYNC0-to-task interval | 999-1001 us |

The AS5600's I2C reads used to run inside the EtherCAT task and cost up to
1071 us -- longer than the cycle -- which also showed up as +/-60 us of
SYNC0 jitter. They now run in their own task on core 0, woken at SYNC0,
so the sample instant stays on the DC clock and the value arrives one
cycle later (fixed latency).

It stops and drops EN at once when the PLC clears enable, when the slave
leaves OP or SYNC0 stops for 5 ms (fault 1), or when one cycle asks for
more than 150 steps (fault 2 -- also what happens if enable comes with a
target that is not the actual position). Faults latch until reset with
enable off.

On the PLC:

```
PRG_EcatStepper.xEnable     := TRUE;
PRG_EcatStepper.rTargetDeg  := 90.0;    // goes there, trapezoidal profile
PRG_EcatStepper.rMaxVelDeg  := 180.0;   // deg/s
PRG_EcatStepper.rAccDeg     := 1800.0;  // deg/s^2
// -> rActualDeg, xInPosition, xFault, byFault; xSetZero, xReset
```

`udiStepsPerRev` must match the driver: 6400 = 200-step motor at 32
microsteps, 0.05625 deg per step, so any angle lands within +/-0.028 deg.
16 microsteps (3200) is too coarse for 0.1 deg. For an exact 0.1 deg grid
use a steps/rev divisible by 3600.

Verified without a motor (PCNT hardware count read back): 90 deg -> 1600
steps, -45 deg -> -800, 0.1 deg -> 2 steps (0.1125 deg), 1800 deg at
3000 deg/s (53 steps/ms, above the old 50 kHz ceiling) -> 32000, back to
0 -> 0. No faults.

## Process data

| Direction | Bytes | Content |
|---|---|---|
| Slave -> master (`BufferIn`) | 0 | Heartbeat counter, +1 per cycle |
| | 1 | Echo of output byte 1 |
| | 2-3 | Encoder angle 0-4095 (UINT, little-endian), filtered |
| | 4 | AS5600 STATUS: bit5 magnet detected, bit4 too weak, bit3 too strong; bit0 = sensor not answering |
| | 5-8 | Multi-turn position in counts (DINT, little-endian), from power-up |
| | 9 | I2C error counter |
| | 10 | AGC |
| | 11-12 | Max task interval over the last second, us |
| | 13-14 | Min task interval, us |
| | 15 | 1 = driven by SYNC0, 0 = polling fallback |
| | 16-19 | Stepper actual position, steps emitted (DINT) |
| | 20 | Stepper status: bit0 enabled, bit1 fault, bit2 moving |
| | 21 | Stepper fault: 0 none, 1 comm lost, 2 setpoint jump |
| Master -> slave (`BufferOut`) | 0 | Bit 0 drives the ESP32's on-board LED (GPIO2) |
| | 1 | Echoed on input byte 1 |
| | 2 | Stepper control: bit0 enable, bit1 fault reset |
| | 4-7 | Stepper target position in steps (DINT) |

On the PLC, `PRG_EcatEsp` decodes these and `http://192.168.1.70:8126/`
shows them live.

LED: blinking 1 Hz = EasyCAT `Init` failing and retrying; otherwise it
follows output bit 0.

Serial also prints one JSON status line every 100 ms.

## Sources

`lib/EasyCAT` is AB&T's EasyCAT library V2.1 (examples removed),
`esi/EasyCAT_V2_0.xml` the DC-capable ESI shipped with EasyConfigurator
V4.3, and `esi/EasyCAT_PRO.xml` the older website ESI (kept for
reference, no DC), all from https://www.bausano.net/en/download-2. The library runs on ESP32 through
its generic Arduino path (`SPI.transfer`, `digitalWrite`).

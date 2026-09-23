# EasyCAT PRO + ESP32 -- EtherCAT slave

An ESP32 behind an AB&T EasyCAT PRO (LAN9252) as a custom EtherCAT slave.
Standard 32+32 byte process image, matching the PRO's stock EEPROM and
`esi/EasyCAT_PRO.xml` (vendor `0x079A` AB&T, product `0x00DEFEDE`
"EasyCAT 32+32").

## Wiring

| EasyCAT PRO revB | ESP32 | Signal |
|---|---|---|
| 3.3 | 3V3 | Power -- **never 5V/VIN** |
| G | GND | Ground |
| MI | GPIO19 | VSPI MISO |
| MO | GPIO23 | VSPI MOSI |
| SCK | GPIO18 | VSPI SCK |
| SCS | GPIO5 | Chip select |
| INT | GPIO17 | Interrupt (unused, ASYNC) |
| SYN0 | GPIO16 | DC SYNC0 (unused, ASYNC) |
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

**Pin 1 is on the latch side** of the XH connector (the vendor manual:
"左边倒钩起为 Pin1"). Counted from the other end the encoder gets no power
and the boot scan finds nothing on either SDA/SCL order.

## Process data

| Direction | Bytes | Content |
|---|---|---|
| Slave -> master (`BufferIn`) | 0 | Heartbeat counter, +1 per cycle |
| | 1 | Echo of output byte 1 |
| | 2-3 | Encoder raw angle 0-4095 (UINT, little-endian) |
| | 4 | AS5600 STATUS: bit5 magnet detected, bit4 too weak, bit3 too strong; bit0 = sensor not answering |
| | 5-8 | Multi-turn position in counts (DINT, little-endian), from power-up |
| | 9 | I2C error counter |
| | 10 | AGC |
| Master -> slave (`BufferOut`) | 0 | Bit 0 drives the ESP32's on-board LED (GPIO2) |
| | 1 | Echoed on input byte 1 |

On the PLC, `PRG_EcatEsp` decodes these and `http://192.168.1.70:8126/`
shows them live.

LED: blinking 1 Hz = EasyCAT `Init` failing and retrying; otherwise it
follows output bit 0.

Serial also prints one JSON status line every 100 ms.

## Sources

`lib/EasyCAT` is AB&T's EasyCAT library V2.1 (examples removed) and
`esi/EasyCAT_PRO.xml` the EasyCAT PRO ESI, both from
https://www.bausano.net/en/download-2. The library runs on ESP32 through
its generic Arduino path (`SPI.transfer`, `digitalWrite`).

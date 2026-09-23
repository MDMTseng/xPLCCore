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

## Process data

| Direction | Bytes | Content |
|---|---|---|
| Slave -> master (`BufferIn`) | 0 | Free-running counter, proves the slave is alive |
| | 1-31 | Loopback of the master's output bytes 1-31 |
| Master -> slave (`BufferOut`) | 0 | Bit 0 drives the ESP32's on-board LED (GPIO2) |
| | 1-31 | Echoed back on the inputs |

LED behaviour: blinking 1 Hz = `Init` failing and retrying; off or
following output bit 0 = `Init` succeeded.

## Sources

`lib/EasyCAT` is AB&T's EasyCAT library V2.1 (examples removed) and
`esi/EasyCAT_PRO.xml` the EasyCAT PRO ESI, both from
https://www.bausano.net/en/download-2. The library runs on ESP32 through
its generic Arduino path (`SPI.transfer`, `digitalWrite`).

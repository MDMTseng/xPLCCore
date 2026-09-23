# Flexible feeder (3rd-gen vibration bowl) -- Modbus RTU

Source: the vendor's `震动盘3代MODBUS命令.xlsx` (not in the repo; this is
the transcription that matters) and the PC-side driver that runs the
machine today: `PluginHello.tsx` `flexVibCtrl()` over
`script/serial_ctrl.py` (pymodbus).

Link: RTU, 19200 8N1, slave address 1, 1 s timeout. The PLC-side status
is in [`ethercat_config.md`](./ethercat_config.md#rs-485--modbus-rtu).

## Two address spaces

The same address means different things depending on the function code.

| Space | Function codes | Holds |
|---|---|---|
| Data | **FC03 read / FC16 (0x10) write** -- "cannot be written with 06" | Parameters |
| Command | **FC06 write only** -- "cannot be read, cannot be written with 10" | Start/stop |

## Command space (FC06)

| Address | Value | Meaning |
|---|---|---|
| `0x0001`-`0x000B` | 1 start / 0 stop | Single action 1-11 (see below) |
| `0x000C` | `0x0000`-`0x000F` | Backlight, one bit per RGBW channel |
| `0x000F` | 2 | Stop the current action |
| `0x0101` | 1-5 | Run combined-action page 1-5, stops by itself |
| `0x001D` | 1 start / 0 stop | Hopper linear vibrator |

Actions: 1 up-left, 2 up, 3 up-right, 4 left, **5 scatter**, 6 right,
7 down-left, 8 down, 9 down-right, **10 horizontal**, **11 vertical**.

What the machine uses today (`CalibPage.tsx`, `MiscControlsPage.tsx`):

| Call | Write | Meaning |
|---|---|---|
| `FVib(5, ms)` | `0x0005` = 1, wait, = 0 | Scatter |
| `FVib(10, ms)` | `0x000A` | Horizontal |
| `FVib(11, ms)` | `0x000B` | Vertical |
| `von/voff(0x1D)`, `FVib(0x1D, ms)` | `0x001D` | Hopper vibrator |
| `top_light_on/off` | `0x000C` = 8 / 0 | Backlight (bit 3) |

## Data space (FC03 / FC16)

| Address | Content | Range |
|---|---|---|
| `0x0000`-`0x0036` | Actions 1-11, 5 registers each: frequency, amplitude 1-4 | 100-1000 = 10.0-100.0 Hz / % |
| `0x0037` | UDP/TCP address | 1-247 |
| `0x0038` | RS-232 baud (high byte, 1-8 = 1200-115200) / address (low byte) | |
| `0x0039` | RS-485 baud (high byte) / address (low byte) | 19200 + 1 = `0x0501` |
| `0x003A` / `0x003B` | Hopper vibrator frequency / amplitude | 40-400 / 100-1000 |
| `0x003C`-`0x003F` | Backlight W, R, G, B brightness | 0-100 |
| `0x0040` | Hopper vibrator delay | 0-1000 |
| `0x0041`-`0x0043` | Auto-scan start frequency, amplitude, cycle/step | |
| `0x0044` | Waveform: 0 square, 1 sine | |
| `0x0046`-`0x0077` | Combined-action pages 1-5: 5 x (action no. 1-11, delay 0-6000) | |
| `0x00AA`-`0x00AD` | IO inputs 1-4: type (high byte) / number (low byte) | |
| `0x00B4`-`0x00B7` | IO outputs 1-4: state (bits 15-14) / delay (bits 13-0) | |
| `0x00BE` | Temperature protection | 0-100 |
| `0x00E8` | Vibration status | |
| `0x0100`-`0x022B` | Recipes M1-M10: names (ASCII) and data | |

Vibration status is read through the IO outputs: IO1 = combined-action
running, IO2 = single-action running.

Mapping `0x0039`'s baud code 5 to 19200 assumes the usual
1200/2400/4800/9600/19200/38400/57600/115200 order; the sheet gives the
range, not the table.

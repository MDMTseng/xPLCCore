# Industrial Packaging Machine - Operation Guide

## Purpose

This guide describes the normal operator flow for the UI in this project.
The latest UI design focuses on low-noise operation:

1. Show only critical controls in default view.
2. Keep setup and status in clear cards.
3. Move engineering tools under collapsed advanced sections.
4. Preserve all machine logic and test actions behind those sections.

---

## System Communication Channels

The machine uses 3 communication links:

- `TCP (PLC)` - Main machine/motion control (CodeSys PLC)
- `TCP2 (Vision)` - Vision inspection system
- `Modbus (Feeder)` - Feeder/peripheral control

All three must be connected before production controls are unlocked.

---

## Connection UI (Top Area)

The connection area shows a compact setup card and grouped 3-dot indicator:

- **Green**: Connected
- **Yellow**: Connecting
- **Red**: Disconnected

### How to configure connections

1. Click **Configure** (dot group button).
2. The **Connection Setup** modal opens.
3. Configure each section:
   - TCP (host/port)
   - TCP2 (host/port)
   - Modbus (COM/baud)
4. Connect each channel.
5. Verify all three dots are green.

When all three are connected, Step 1 is complete and controls are unlocked.

---

## Stage Workflow

The UI now uses a stage-oriented layout:

- Left stage navigator for `Welcome`, `Calib`, `Operation`
- Main content canvas for the selected stage
- Operator-first controls in each stage
- Engineering tools moved into collapsed "Engineering Console" sections

## Step 1 - Connections

Complete in top connection area first.

Requirement:

- TCP connected
- TCP2 connected
- Modbus connected

If any channel is missing, machine controls remain locked.

## Step 2 - PLC Motion Initialization (Welcome tab)

Go to `Welcome` tab and run:

- `Init PLC Motion`

What it does (high level):

- Drives PLC state machine toward ready state
- Handles power/group/home transitions
- Sets coordinate reference and returns to ready posture

Safety/override:

- `EnterError` can be used to force motion system into error state (motor off path).

After successful init, the workflow marks PLC as ready and allows production tabs.

## Step 3 - Production (Calib tab)

Main operator actions are grouped in one compact control card:

- `RUN` - Starts automatic production cycle
- `STOP` - Stops/quits action state safely

Live info shown during run:

- Pack counter / speed summary
- Running state text
- Toss/error info when relevant

`Check Plate` section remains available for in-process recheck actions.

---

## Important Runtime Behaviors

## RUN behavior

During RUN, logic executes cyclically:

- Feeder check and object candidate selection
- Pick/place motion
- Vision checks (side/bottom/top)
- Compensation and placement decision
- Toss path for NG conditions
- Reel and input watchdog checks

## STOP behavior

STOP performs controlled stop behavior:

- Requests cycle stop
- Releases pause/step waits
- Drives to safe sequence when applicable
- Ensures nozzle release behavior in stop path

---

## Error Handling

Typical error paths include:

- PLC state errors during initialization
- Input watchdog faults (material/sensor conditions)
- Vision compensation out-of-range checks

Operator actions:

- Use `STOP` to terminate production
- Use `EnterError` in Welcome when manual fault entry is required
- Clear physical issue, then re-run from Step 1/2 as needed

---

## Hidden but Available Advanced Tools

For UI cleanliness, test/debug controls are intentionally hidden by default.

Where to find them:

- `Welcome` -> **Advanced / Test Controls**
- `Calib` -> **Advanced setup and test tools**
- `Calib` -> **Calibration records and object debug data**

These sections still provide full access for engineering/debug usage.

---

## Recommended Standard Operating Sequence

1. Open UI and verify runtime environment is healthy.
2. Click connection dot group -> connect TCP, TCP2, Modbus.
3. Confirm all dots are green.
4. Open Welcome -> run `Init PLC Motion`.
5. Open Calib -> press `RUN`.
6. Monitor status/counter/toss info during operation.
7. Use `Check Plate` when recheck is required.
8. Press `STOP` to exit action state safely.

---

## Quick Troubleshooting

- **Cannot enter production tabs**
  - Check connection dots; all 3 must be connected.
  - Run `Init PLC Motion` in Welcome.

- **TCP/TCP2 not connecting**
  - Verify host/port in Connection Setup modal.
  - Confirm remote service is running.

- **Modbus not connecting**
  - Verify COM port and baud rate.
  - Confirm serial device ownership/cable/power.

- **Cycle not behaving as expected**
  - Check running state text and toss info.
  - Use advanced sections for deeper diagnostics.


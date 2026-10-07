"""The machine's EtherCAT topology and PLC address, in one place for the host
tools. Only SLAVE_NAMES is maintained by hand: the CODESYS device tree, in
wire order (the master assigns station 1001, 1002, ... in tree order), and
every station below is derived from it. 2026-10-07 the owner re-cabled to
ASDA x3 -> QEC -> reel -> EC0808DN -> EasyCAT (jobs/templates/
reorder_2026_10_07.py); the PLC side reads its stations from the master
(PhysSlaveAddr) and GVL's taps follow by channel name (sync_io_taps.py).
Check a station's identity (0x1018) before writing to it.

    import topology as tp
    tp.DRIVE_STATIONS[1]   # EAxis1 -> 1002
    tp.SLAVE_NAMES         # IoConfig_Globals names, tree order
    tp.PLC_HOST            # from codesys_scripts/codesys_env.json
"""

import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(key, default):
    try:
        with open(os.path.join(REPO, "codesys_scripts", "codesys_env.json"), encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError):
        return default


PLC_HOST = _env("plc_host", "192.168.1.70")
PLC_PORT = int(_env("plc_port", 8125))

# IoConfig_Globals instance names, device tree order = wire order.
SLAVE_NAMES = [
    "ASDA_B3_E_CoE_Drive",      # EAxis0
    "ASDA_B3_E_CoE_Drive_1",    # EAxis1
    "ASDA_B3_E_CoE_Drive_2",    # EAxis2
    "QEC_R11MP3S_V",
    "reel_pull_motor",
    "EC0808DN",
    "EasyCAT",
]
STATION_OF = dict((name, 1001 + i) for i, name in enumerate(SLAVE_NAMES))
SLAVES = [(name, STATION_OF[name]) for name in SLAVE_NAMES]

# The delta trio: axis index (EAxis0..2, SoftMotion) -> slave, station.
DRIVE_SLAVES = {0: "ASDA_B3_E_CoE_Drive", 1: "ASDA_B3_E_CoE_Drive_1", 2: "ASDA_B3_E_CoE_Drive_2"}
DRIVE_STATIONS = dict((k, STATION_OF[name]) for k, name in DRIVE_SLAVES.items())
REEL_STATION = STATION_OF["reel_pull_motor"]

# Identity 0x1018:01 of the ASDA-B3 drives: check it before any SDO write.
DELTA_VENDOR = 0x1DD

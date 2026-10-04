"""The machine's EtherCAT topology and PLC address, in one place for the host
tools. Everything here moves when the CODESYS project tree changes (the
slave reorder of 2026-10-02 shifted the station addresses and the old 1003
became the reel): keep it in step with the tree, which must equal the wire
order (doc_review/asda_stale_target_2026-09-30.md, 7l).

    import topology as tp
    tp.DRIVE_STATIONS[1]   # EAxis1 -> 1005
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

# (IoConfig_Globals instance, EtherCAT station address), tree = wire order.
SLAVES = [
    ("EC0808DN", 1001),
    ("QEC_R11MP3S_V", 1002),
    ("reel_pull_motor", 1003),
    ("ASDA_B3_E_CoE_Drive", 1004),
    ("ASDA_B3_E_CoE_Drive_1", 1005),
    ("ASDA_B3_E_CoE_Drive_2", 1006),
    ("EasyCAT", 1007),
]
SLAVE_NAMES = [name for name, _ in SLAVES]
STATION_OF = dict(SLAVES)

# The delta trio: axis index (EAxis0..2, SoftMotion) -> station.
DRIVE_STATIONS = {0: 1004, 1: 1005, 2: 1006}
DRIVE_SLAVES = {0: "ASDA_B3_E_CoE_Drive", 1: "ASDA_B3_E_CoE_Drive_1", 2: "ASDA_B3_E_CoE_Drive_2"}
REEL_STATION = 1003

# Identity 0x1018:01 of the ASDA-B3 drives: check it before any SDO write.
DELTA_VENDOR = 0x1DD

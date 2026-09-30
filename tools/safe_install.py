"""Download the project to the PLC with every safety step (machine.safe_install):
1. save the PLC log (500 entries; skip with --no-log);
2. FSM to UnInited, and check every delta axis is powered off;
3. `rpc.py install --on-site`;
4. relink the UI;
5. check the EtherCAT master finished its startup.

    python tools/safe_install.py [--no-log]

After it, the delta is virtual (project default); `tools/joint_bench.py
real` makes it real for this PLC run. Code-only changes can still go
through `rpc.py push` (online change); device / task configuration needs
this full download.
"""

import argparse

import machine as mc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-log", action="store_true", help="do not save the PLC log first")
    a = ap.parse_args()
    mc.reconnect()
    mc.safe_install(read_log=not a.no_log)


if __name__ == "__main__":
    main()

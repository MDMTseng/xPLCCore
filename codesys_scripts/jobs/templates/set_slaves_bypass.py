# -*- coding: ascii -*-
# Bypass the QEC (A axis stepper, station 1006) and the reel servo (1002)
# on the EtherCAT line: disable both slaves in the device tree and make
# their axes (EAXIS_A, SM_Drive_GenericDSP402 = A, reelpullmotor) virtual, so the PLC code and the
# delta kinematics (SpiderR chains A) still have them. With BYPASS = False
# both come back (slaves enabled, axes real). Offline edit + save; deploy
# with tools/safe_install.py after the wiring matches.
#
#   rpc.py exec --file jobs/templates/set_slaves_bypass.py
#
# With both bypassed the first DC slave is EAxis0: it becomes the reference
# clock (tested 2026-10-01, no change to the drives' behaviour).

BYPASS = True
SLAVES = ("QEC_R11MP3S_V", "reel_pull_motor")
AXES = ("EAXIS_A", "SM_Drive_GenericDSP402", "reelpullmotor")   # SM_Drive_GenericDSP402 = the A rotation (QEC axis 2); EAXIS_A = QEC axis 1, unwired


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
for name in SLAVES:
    dev = proj.find(name, True)
    if not dev:
        raise Exception("%s not found" % name)
    dev = dev[0]
    before = dev.is_enabled()
    if BYPASS and before:
        dev.disable()
    elif not BYPASS and not before:
        dev.enable()
    print("%s enabled: %s -> %s" % (name, before, dev.is_enabled()))

for name in AXES:
    found = proj.find(name, True)
    if not found:
        raise Exception("%s not found" % name)
    ax = found[0]
    done = False
    for c in ax.connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for prm in prms:
            if t(prm.name) == "bVirtual":
                old = t(prm.value)
                want = "TRUE" if BYPASS else "FALSE"
                if old.upper() != want:
                    prm.value = want
                print("%s bVirtual: %s -> %s" % (name, old, t(prm.value)))
                done = True
    if not done:
        print("%s: no bVirtual parameter found" % name)
proj.save()
print("saved")

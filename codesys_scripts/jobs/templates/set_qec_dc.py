# -*- coding: ascii -*-
# Turn DC on / off for the QEC stepper slave and, with SLAVES, others (the
# reel, EC0808DN). The EtherCAT master takes the first DC slave in the
# CONFIGURED (device tree) order as the reference clock -- not the first on
# the wire (2026-10-02, doc_review/asda_stale_target_2026-09-30.md 7l).
# Stations since the 2026-10-02 reorder (tree = wire): EC0808DN 1001,
# QEC 1002, reel 1003, EAxis0..2 1004..1006, EasyCAT 1007.
# Offline edit + save; deploy with tools/safe_install.py (drives off).
#
#   rpc.py exec --file jobs/templates/set_qec_dc.py
#
# DC_ON = True is the machine's normal setting (DC enable 1, sync0 enable 1,
# DCSetting 1). 2026-10-01: off as a trial, to see whether the ASDA
# stale-target bursts follow the reference clock. EAXIS_A (on the QEC)
# then runs without SYNC0; do not move it during the trial.

DC_ON = True
SLAVES = ("QEC_R11MP3S_V",)    # add "reel_pull_motor" to move the reference to EAxis0
VALUES = {"DC enable": "1" if DC_ON else "0",
          "DC sync0 enable": "1" if DC_ON else "0",
          "DCSetting": "1" if DC_ON else "0"}


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
changed = []
for SLAVE in SLAVES:
    dev = proj.find(SLAVE, True)
    if not dev:
        raise Exception("%s not found" % SLAVE)
    for c in dev[0].connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for prm in prms:
            pn = t(prm.name)
            if pn in VALUES:
                old = t(prm.value)
                if old != VALUES[pn]:
                    prm.value = VALUES[pn]
                changed.append("%s %s: %s -> %s" % (SLAVE, pn, old, t(prm.value)))
for line in changed:
    print(line)
proj.save()
print("saved")

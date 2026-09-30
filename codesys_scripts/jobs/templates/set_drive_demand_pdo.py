# -*- coding: ascii -*-
# Put the drive's position demand value (0x6062, what the drive's control
# loop actually uses) into its TxPDO, in place of the touch probe position
# 0x60BA (same size, unused here), so the PLC sees per cycle what the ASDA
# did with the target it was sent (the stale-target investigation,
# doc_review/asda_stale_target_2026-09-30.md). Offline edit + save; deploy
# with the delta off and `rpc.py install --on-site`.
#
#   rpc.py exec --file jobs/templates/set_drive_demand_pdo.py
#
# The IEC channel keeps its name ("Touch Probe Pos1 Pos Value") but carries
# 0x6062. RESTORE = True puts 0x60BA back.

DRIVES = ("ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2")
RESTORE = False
PDO_ID = 1879376128            # header of 0x1A01 (the TxPDO assigned to SM3)
FROM = "16#60BA" if not RESTORE else "16#6062"
TO = "16#6062" if not RESTORE else "16#60BA"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


out = []


def walk(o):
    try:
        name = t(o.get_name())
        if o.is_device and name in DRIVES:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    if PDO_ID < prm.id < PDO_ID + 16 and t(prm.value).startswith("{" + FROM + ","):
                        old = t(prm.value)
                        prm[0].value = TO          # struct: edit the Index field
                        out.append("%s id %d: %s -> %s" % (name, prm.id, old, t(prm.value)))
        for ch in o.get_children():
            walk(ch)
    except Exception as e:
        out.append("error %s" % e)


proj = projects.primary
for top in proj.get_children():
    walk(top)
for line in out:
    print(line)
if out and not any(l.startswith("error") for l in out):
    proj.save()
    print("saved")

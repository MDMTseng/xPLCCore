# -*- coding: ascii -*-
# 2026-10-07: the owner re-cabled the bus to
#   PLC -> EAxis0, EAxis1, EAxis2 (ASDA) -> QEC -> reel -> EC0808DN -> EasyCAT
# (read back from the master's AutoIncAddr: 0, -1, -2, -3, -4, -5, -6).
# This job, offline (tools/deploy.py --post-import, after the import):
#   1. puts the device tree in that wire order (set_slave_order.py's way;
#      non-optional), so the DC reference clock -- the first DC slave in the
#      CONFIGURED order -- is EAxis0 and the stations become
#      EAxis0..2 1001..1003, QEC 1004, reel 1005, EC0808DN 1006, EasyCAT 1007;
#   2. moves GVL's AT taps to the drives' new channel addresses, found by
#      channel name (sync_io_taps.py; also rewrites codesys_code GVL.st);
#   3. prints the drives' channel addresses before / after and the QEC's
#      children and DC settings (they must not change);
#   4. builds and prints "build errors: N".
#
# NOT done here: the QEC ESI update (firmware/qec/esi). Scripted
# dev.update() dropped the QEC's SoftMotion axes (EAXIS_A,
# SM_Drive_GenericDSP402) and reset DCSetting 1 -> 0 in the rehearsal.
#
#   rpc.py exec --file jobs/templates/reorder_2026_10_07.py

import os
from System import Guid

ORDER = ["ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2",
         "QEC_R11MP3S_V", "reel_pull_motor", "EC0808DN", "EasyCAT"]
DRIVES = ORDER[:3]
QEC = "QEC_R11MP3S_V"


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
master = proj.find("EtherCAT_Master_SoftMotion", True)[0]


def channels(name):
    out = []
    d = proj.find(name, True)[0]
    for c in d.connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for p in prms:
            try:
                m = p.io_mapping
            except Exception:
                m = None
            if m is None:
                continue
            try:
                auto = t(m.automatic_iec_address)
            except Exception:
                auto = "?"
            try:
                man = t(m.manual_iec_address)
            except Exception:
                man = "?"
            out.append((t(p.name), auto, man))
    return out


def dump_qec():
    q = proj.find(QEC, True)[0]
    di = q.get_device_identification()
    print("QEC identity: type %s id %s version %s" % (di.type, t(di.id), t(di.version)))
    for c in q.connectors:
        for p in c.host_parameters:
            n = t(p.name)
            if n.startswith("DC") or n.startswith("Number of"):
                print("  QEC %-28s = %s" % (n, t(p.value)))
    for ch in q.get_children(False):
        cdi = ch.get_device_identification()
        print("  child %-24s type %s id %s" % (t(ch.get_name()), cdi.type, t(cdi.id)))
        if cdi.type == 1027:
            for c in ch.connectors:
                for p in c.host_parameters:
                    n = t(p.name)
                    if n in ("wDriveID", "dwRatioTechUnitsDenom", "iRatioTechUnitsNum", "fSWMaxVelocity",
                             "ScalingIncs", "ScalingUnits", "Logical device number"):
                        print("    %-24s = %s" % (n, t(p.value)))


print("order before: " + ", ".join(t(d.get_name()) for d in master.get_children(False)))
before = dict((n, channels(n)) for n in DRIVES)
dump_qec()

for i, name in enumerate(ORDER):
    proj.find(name, True)[0].move(master, i)
for name in ORDER:
    d = proj.find(name, True)[0]
    for c in d.connectors:
        for p in c.host_parameters:
            if t(p.name) == "Optional" and t(p.value) != "False":
                p.value = "False"
print("order after:  " + ", ".join(t(d.get_name()) for d in master.get_children(False)))

dump_qec()

for n in DRIVES:
    after = channels(n)
    for (pn, a0, m0), (pn1, a1, m1) in zip(before[n], after):
        mark = "" if (a0, m0) == (a1, m1) else "   <-- moved"
        print("ADDR %s / %s: auto %s -> %s, manual '%s' -> '%s'%s" % (n, pn, a0, a1, m0, m1, mark))

SYNC_TAPS_WRITE = True
execfile(os.path.join(_HERE, "jobs", "templates", "sync_io_taps.py"))

proj.save()
print("saved")

B = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
system.clear_messages(B)
proj.active_application.generate_code()
errs = [m for m in system.get_message_objects(B) if "error" in str(m.severity).lower()]
for m in errs:
    print("[ERR] %s" % t(m.text))
print("build errors: %d" % len(errs))

# -*- coding: ascii -*-
# Remove the leftover manual mapping of EAxis1's Target Position PDO
# (ASDA_B3_E_CoE_Drive_1) to the unused variable biSetPos at %QD16, and
# give the channel an automatic address again (2026-10-02; noted as a
# leftover in doc_review/asda_stale_target_2026-09-30.md). Prints the
# drives' tapped channel addresses afterwards: the GVL taps must follow.
# Offline edit + save.
#
#   rpc.py exec --file jobs/templates/clear_biSetPos.py

DRIVES = ["ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2"]
TAPPED = ("Control Word", "Target Position", "Status Word", "Actual Position",
          "Actual Torque", "Touch Probe Pos1 Pos Value")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def channels(name):
    d = projects.primary.find(name, True)[0]
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
            if m is not None:
                yield t(p.name), m


for n, m in channels("ASDA_B3_E_CoE_Drive_1"):
    if n == "Target Position":
        print("before: var=%s addr=%s auto=%s" % (t(m.variable), t(m.manual_iec_address), t(m.automatic_iec_address)))
        m.variable = ""
        m.automatic_iec_address = True
        print("after:  var=%s addr=%s auto=%s" % (t(m.variable), t(m.manual_iec_address), t(m.automatic_iec_address)))
for name in DRIVES:
    for n, m in channels(name):
        if n in TAPPED:
            print("A %-22s %-28s %s" % (name, n, t(m.manual_iec_address)))
projects.primary.save()
print("saved")

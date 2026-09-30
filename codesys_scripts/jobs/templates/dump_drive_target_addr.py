# -*- coding: ascii -*-
# READ-ONLY. Print the IEC address (and any mapping) of each ASDA drive's
# Target Position / Actual Position PDO channel (manual_iec_address
# holds the address even when it is assigned automatically), for AT-declared read-only
# taps of what SoftMotion puts in the process image.
#
#   rpc.py exec --readonly --file jobs/templates/dump_drive_target_addr.py

DRIVES = ("ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def scan(o):
    try:
        name = t(o.get_name())
        if o.is_device and name in DRIVES:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    pn = t(prm.name)
                    if "Position" in pn and getattr(prm, "is_mappable_io", False):
                        addr = "?"
                        for attr in ("manual_iec_address",):
                            try:
                                v = getattr(prm.io_mapping, attr)
                                if v:
                                    addr = "%s=%s" % (attr, t(v))
                                    break
                            except Exception:
                                pass
                        var = ""
                        try:
                            var = t(prm.io_mapping.variable)
                        except Exception:
                            pass
                        print("%-24s %-28s %-7s %s var=%s" % (name, pn, t(getattr(prm, "channel_type", "?")), addr, var))
        for ch in o.get_children():
            scan(ch)
    except Exception as e:
        pass


for top in projects.primary.get_children():
    scan(top)

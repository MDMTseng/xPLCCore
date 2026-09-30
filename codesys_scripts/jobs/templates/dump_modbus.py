# -*- coding: ascii -*-
# READ-ONLY. Every parameter of the Modbus devices (COM port, client,
# slaves, channels), for timing questions.
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/dump_modbus.py


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def walk(o, under):
    try:
        name = t(o.get_name())
        mod = under or ("Modbus" in name)
        if o.is_device and mod:
            print("DEVICE %s" % name)
            for c in o.connectors:
                try:
                    for prm in c.host_parameters:
                        v = t(prm.value)
                        if v not in ("", "0", "FALSE", "False"):
                            print("   %-40s = %s" % (t(prm.name)[:40], v[:100]))
                except Exception:
                    pass
        for c in o.get_children():
            walk(c, mod)
    except Exception:
        pass


for top in projects.primary.get_children():
    walk(top, False)

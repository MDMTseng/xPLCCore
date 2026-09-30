# -*- coding: ascii -*-
# READ-ONLY. Sub-elements of one PDO entry parameter (EAxis0 drive, 0x1A01,
# the 0x60BA entry), to learn how to edit it.


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def walk(o):
    if o.is_device and t(o.get_name()) == "ASDA_B3_E_CoE_Drive":
        for c in o.connectors:
            for prm in c.host_parameters:
                if 1879376128 < prm.id < 1879376144 and "60BA" in t(prm.value):
                    print("param", prm.id, t(prm.value))
                    print("attrs", [a for a in dir(prm) if not a.startswith("_")])
                    try:
                        n = prm.Count
                        print("Count", n)
                        for i in range(n):
                            e = prm[i]
                            print("  [%d] %s = %s (%s)" % (i, t(getattr(e, "identifier", "?")), t(e.value), t(getattr(e, "type_string", ""))))
                    except Exception as ex:
                        print("index error", ex)
        return True
    for ch in o.get_children():
        if walk(ch):
            return True
    return False


for top in projects.primary.get_children():
    if walk(top):
        break

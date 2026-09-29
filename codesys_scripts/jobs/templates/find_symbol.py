# -*- coding: ascii -*-
# READ-ONLY. Find where a symbol is declared or bound in the open project:
# every object's declaration / implementation text, and every device IO
# mapping (EtherCAT, Modbus, local IO ...). For names that are not in the
# exported .st files (e.g. IOHUB_mot0_limit).
#
#   rpc.py exec --readonly --file jobs/templates/find_symbol.py
#
# Edit NEEDLES below.

NEEDLES = ["IOHUB_mot"]

proj = projects.primary


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def path_of(o):
    names = []
    cur = o
    for _ in range(12):
        try:
            names.append(t(cur.get_name()))
            cur = cur.parent
        except Exception:
            break
    return "/".join(reversed(names))


def walk(o):
    yield o
    try:
        for c in o.get_children():
            for x in walk(c):
                yield x
    except Exception:
        pass


hits = 0
for o in walk(proj):
    for attr in ("textual_declaration", "textual_implementation"):
        try:
            txt = t(getattr(o, attr).text)
        except Exception:
            continue
        for i, line in enumerate(txt.splitlines()):
            if any(n in line for n in NEEDLES):
                print("TEXT %s [%s:%d] %s" % (path_of(o), attr, i + 1, line.strip()))
                hits += 1
    # device IO mappings
    try:
        conns = o.connectors
    except Exception:
        conns = None
    if conns:
        for c in conns:
            try:
                prms = c.host_parameters
            except Exception:
                continue
            for prm in prms:
                try:
                    var = t(prm.io_mapping.variable)
                except Exception:
                    continue
                if var and any(n in var for n in NEEDLES):
                    print("MAP  %s  param %s (%s) -> %s" % (path_of(o), t(prm.name), t(prm.id), var))
                    hits += 1
print("hits: %d" % hits)

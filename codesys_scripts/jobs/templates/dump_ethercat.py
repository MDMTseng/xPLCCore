# -*- coding: ascii -*-
# READ-ONLY. Dump the EtherCAT configuration of the open project.
#
#   rpc.py exec --readonly --file jobs/templates/dump_ethercat.py
#
# For every device under the EtherCAT master: identity, the parameters
# that have been changed away from their default, and every bound IO
# mapping. Parameters still at their default are counted but not listed
# -- a slave like EC0808DN carries 294 of them and printing all of it
# buries the handful of decisions someone actually made.
#
# Pass "--all" style behaviour by flipping SHOW_DEFAULTS below if you
# need the complete picture for a single device.

SHOW_DEFAULTS = False
MAX_PARAMS_PER_CONNECTOR = 400


def p(s):
    try:
        if isinstance(s, unicode):
            s = s.encode("utf-8", "replace")
        print(s)
    except Exception:
        try:
            print(repr(s))
        except Exception:
            pass


def t(v):
    """Coerce anything to a printable str without exploding on unicode."""
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def nm(o):
    try:
        return t(o.get_name())
    except Exception:
        return "<unnamed>"


def short(s, n):
    s = t(s).replace("\n", " ").replace("\r", " ")
    return s if len(s) <= n else s[:n - 1] + "~"


proj = projects.primary
p("project: %s" % t(proj.path))
p("")

# ---- locate the EtherCAT master ---------------------------------------
master = None
stack = list(proj.get_children())
while stack:
    o = stack.pop(0)
    try:
        if o.is_device and "ethercat" in nm(o).lower() and "master" in nm(o).lower():
            master = o
            break
    except Exception:
        pass
    try:
        stack.extend(list(o.get_children()))
    except Exception:
        pass

if master is None:
    # Fall back to any device whose id type is 64 (EtherCAT master).
    stack = list(proj.get_children())
    while stack:
        o = stack.pop(0)
        try:
            if o.is_device and o.get_device_identification().type == 64:
                master = o
                break
        except Exception:
            pass
        try:
            stack.extend(list(o.get_children()))
        except Exception:
            pass

if master is None:
    p("no EtherCAT master found in this project")
    raise SystemExit(0)


def ident(o):
    try:
        d = o.get_device_identification()
        return "type=%s id=%s ver=%s" % (d.type, d.id, d.version)
    except Exception:
        return "type=? id=? ver=?"


def enabled(o):
    try:
        return "enabled" if o.is_enabled() else "** DISABLED **"
    except Exception:
        return "?"


totals = {"devices": 0, "changed": 0, "mapped": 0}


def dump(o, depth):
    totals["devices"] += 1
    pad = "  " * depth
    p("")
    p("%s%s  [%s]" % (pad, nm(o), enabled(o)))
    p("%s  %s" % (pad, ident(o)))

    try:
        cons = list(o.connectors)
    except Exception:
        cons = []

    for ci, c in enumerate(cons):
        try:
            prms = list(c.host_parameters)
        except Exception:
            prms = []
        if not prms:
            continue

        role = t(getattr(c, "connector_role", "?"))
        iface = t(getattr(c, "interface_name", "?"))
        p("%s  connector[%d] %s / %s -- %d parameters"
          % (pad, ci, role, iface, len(prms)))

        changed = []
        mapped = []
        for prm in prms[:MAX_PARAMS_PER_CONNECTOR]:
            try:
                name = t(prm.name)
            except Exception:
                name = "?"
            try:
                val = t(prm.value)
            except Exception:
                val = "?"
            try:
                dflt = t(prm.default_value)
            except Exception:
                dflt = ""

            # Bound IO first -- that is the wiring, not a setting.
            try:
                if prm.is_mappable_io:
                    var = prm.io_mapping.variable
                    if var:
                        mapped.append((name, t(getattr(prm, "channel_type", "?")),
                                       t(getattr(prm, "bit_size", "?")), t(var)))
                    continue
            except Exception:
                pass

            if SHOW_DEFAULTS or (dflt != "" and val != dflt):
                changed.append((name, val, dflt))

        if changed:
            totals["changed"] += len(changed)
            p("%s    -- changed from default --" % pad)
            for name, val, dflt in changed:
                p("%s      %-34s = %-24s (default %s)"
                  % (pad, short(name, 34), short(val, 24), short(dflt, 18)))

        if mapped:
            totals["mapped"] += len(mapped)
            p("%s    -- IO mapping --" % pad)
            for name, chan, bits, var in mapped:
                p("%s      %-30s %-7s %4sbit -> %s"
                  % (pad, short(name, 30), chan, bits, short(var, 46)))

    try:
        kids = list(o.get_children())
    except Exception:
        kids = []
    for k in kids:
        try:
            if k.is_device:
                dump(k, depth + 1)
        except Exception:
            pass


p("=" * 78)
p("ETHERCAT CONFIGURATION")
p("=" * 78)
dump(master, 0)

p("")
p("=" * 78)
p("devices: %d    non-default parameters: %d    bound IO channels: %d"
  % (totals["devices"], totals["changed"], totals["mapped"]))
p("=" * 78)

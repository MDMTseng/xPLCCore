# -*- coding: ascii -*-
# READ-ONLY. Dump the serial / Modbus branch of the device tree.
#
#   rpc.py exec --readonly --file jobs/templates/dump_serial.py
#
# dump_ethercat.py only walks the EtherCAT master, so anything hanging
# off a COM port is invisible to it. This covers the other half: which
# serial ports are configured, at what line settings, and which slaves
# sit on them -- the question you need answered before adding another
# RS-485 device to an existing bus.
#
# Unlike dump_ethercat.py this prints ALL parameters, not just the
# non-default ones: for a serial link the defaults ARE the configuration
# (baud, parity, stop bits) and leaving them out tells you nothing.


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

# Device types that make up a serial/Modbus stack, from the ids seen in
# this project: 92 = Modbus COM, 90 = Modbus client (COM port),
# 91 = Modbus server/slave on that port.
SERIAL_TYPES = (90, 91, 92)

found = []


def scan(o, path):
    here = path + [nm(o)]
    try:
        isdev = bool(o.is_device)
    except Exception:
        isdev = False
    if isdev:
        try:
            d = o.get_device_identification()
            if d.type in SERIAL_TYPES:
                found.append((o, here, d))
        except Exception:
            pass
    try:
        for c in o.get_children():
            scan(c, here)
    except Exception:
        pass


for top in proj.get_children():
    scan(top, [])

if not found:
    p("")
    p("no serial / Modbus devices in this project")
    raise SystemExit(0)

p("")
p("=" * 74)
p("SERIAL / MODBUS DEVICES")
p("=" * 74)

for o, path, d in found:
    p("")
    p("-" * 74)
    p("%s" % " / ".join(path[1:]) if len(path) > 1 else nm(o))
    p("  device id : type=%s id=%s ver=%s" % (d.type, d.id, d.version))
    try:
        p("  enabled   : %s" % o.is_enabled())
    except Exception:
        pass

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
        p("  connector[%d] %s / %s -- %d parameters" % (
            ci, t(getattr(c, "connector_role", "?")),
            t(getattr(c, "interface_name", "?")), len(prms)))
        for prm in prms:
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
            mark = " " if (dflt == "" or val == dflt) else "*"
            line = "   %s %-32s = %s" % (mark, short(name, 32), short(val, 30))
            # Enumerations carry their legal values; for a COM port that
            # is how you learn how many ports the target actually has.
            try:
                if prm.is_enumeration:
                    allowed = prm.allowed_values
                    if allowed:
                        vals = []
                        for a in list(allowed)[:12]:
                            vals.append(short(a, 18))
                        line += "   [%s]" % ", ".join(vals)
            except Exception:
                pass
            p(line)

p("")
p("=" * 74)
p("* = differs from the device description default")
p("serial/Modbus nodes: %d" % len(found))
p("=" * 74)

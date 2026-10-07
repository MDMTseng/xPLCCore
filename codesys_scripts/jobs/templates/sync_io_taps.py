# -*- coding: ascii -*-
# GVL's read-only process-image taps (TapTarget, DemandTap, ActualTap,
# StatusTap, TorqueTap, CtlTap) sit AT fixed %I / %Q addresses, and those
# addresses move whenever the EtherCAT slaves are reordered or a PDO
# changes. This job derives each tap's address from its drive channel
# BY NAME (device + channel), so the taps follow the device tree instead
# of hand-copied numbers (2026-10-07).
#
#   rpc.py exec --readonly --file jobs/templates/sync_io_taps.py   (check only)
#
# Check only by default: prints "tap ... ok" or "tap ... X -> Y" and
# "taps to change: N". With SYNC_TAPS_WRITE = True defined before it runs
# (reorder_2026_10_07.py does), it rewrites the AT addresses in the
# project's GVL and in <source_root>/Application/GVL.st, so the disk
# stays the source of truth.
#
# Why AT taps and not IO-mapped variables: the output channels (Target
# Position, Control Word) belong to SoftMotion's driver; mapping them to a
# variable would make IEC code their writer every cycle.

import os
import re

DRIVES = ["ASDA_B3_E_CoE_Drive", "ASDA_B3_E_CoE_Drive_1", "ASDA_B3_E_CoE_Drive_2"]   # EAxis0..2
TAPS = []
for _k, _d in enumerate(DRIVES):
    TAPS += [("TapTarget%d" % _k, _d, "Target Position"),
             ("DemandTap%d" % _k, _d, "Touch Probe Pos1 Pos Value"),   # 0x6062 mapped here
             ("ActualTap%d" % _k, _d, "Actual Position"),
             ("StatusTap%d" % _k, _d, "Status Word"),
             ("TorqueTap%d" % _k, _d, "Actual Torque")]
TAPS.append(("CtlTap0", DRIVES[0], "Control Word"))

try:
    SYNC_TAPS_WRITE
except NameError:
    SYNC_TAPS_WRITE = False


def _t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def _channel_addr(proj, dev_name, ch_name):
    d = proj.find(dev_name, True)[0]
    for c in d.connectors:
        try:
            prms = list(c.host_parameters)
        except Exception:
            continue
        for p in prms:
            if _t(p.name) != ch_name:
                continue
            try:
                m = p.io_mapping
            except Exception:
                m = None
            if m is not None:
                return _t(m.manual_iec_address)
    raise Exception("no channel '%s' on %s" % (ch_name, dev_name))


def _retarget(text, var, addr):
    pat = re.compile(r"^(\s*%s\s+AT\s+)(%%[IQ][XBWDL]?[0-9.]+)" % re.escape(var), re.M)
    m = pat.search(text)
    if m is None:
        raise Exception("no 'AT' declaration of %s in GVL" % var)
    return m.group(2), text[:m.start(2)] + addr + text[m.end(2):]


def sync_io_taps(proj):
    gvl = proj.find("GVL", True)[0]
    text = _t(gvl.textual_declaration.text)
    changes = 0
    for var, dev, ch in TAPS:
        addr = _channel_addr(proj, dev, ch)
        old, text = _retarget(text, var, addr)
        if old == addr:
            print("tap %-11s %-22s %-28s %s ok" % (var, dev, ch, addr))
        else:
            changes += 1
            print("tap %-11s %-22s %-28s %s -> %s" % (var, dev, ch, old, addr))
    print("taps to change: %d" % changes)
    if changes and SYNC_TAPS_WRITE:
        gvl.textual_declaration.replace(text)
        path = os.path.join(config.source_root(), "Application", "GVL.st")
        f = open(path, "rb")
        disk = f.read()
        f.close()
        crlf = "\r\n" in disk
        disk = disk.replace("\r\n", "\n")
        for var, dev, ch in TAPS:
            disk = _retarget(disk, var, _channel_addr(proj, dev, ch))[1]
        if crlf:
            disk = disk.replace("\n", "\r\n")
        f = open(path, "wb")
        f.write(disk)
        f.close()
        print("taps written to the project GVL and %s" % path)
    return changes


sync_io_taps(projects.primary)

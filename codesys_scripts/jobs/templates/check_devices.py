# -*- coding: ascii -*-
# READ-ONLY. Run with: rpc.py exec --readonly --file jobs/templates/check_devices.py
#
# For every device node in the open project, check whether its device
# description actually exists in THIS machine's repository.
#
# Why this exists: moving the project to a new machine leaves the device
# descriptions behind. They live in C:\ProgramData\CODESYS\Devices, not in
# the .project file, so the project opens and even precompiles fine while
# being unbuildable -- the tree just quietly references descriptions that
# are not installed. On the 2026-09 migration this caught 9 unique missing
# descriptions (the PLC target itself among them) that nothing else flagged.
#
# Placeholders: unpopulated module slots report as (0, 0000 0000, 3.0.0.0).
# They are empty slots, not missing drivers, so they are counted separately
# instead of being reported as problems.

EMPTY_SLOT = "(0, 0000 0000, 3.0.0.0)"


def p(s):
    """IronPython's cStringIO -- which the daemon uses to capture stdout --
    raises on non-ascii unicode, and device names here are not all ascii."""
    try:
        if isinstance(s, unicode):
            s = s.encode("utf-8", "replace")
        print(s)
    except Exception:
        try:
            print(repr(s))
        except Exception:
            pass


def nm(o):
    try:
        n = o.get_name()
        return n.encode("utf-8", "replace") if isinstance(n, unicode) else str(n)
    except Exception:
        return "<unnamed>"


proj = projects.primary
p("project: %s" % proj.path)

rows = []


def walk(o):
    try:
        isdev = bool(o.is_device)
    except Exception:
        isdev = False
    if isdev:
        did = None
        try:
            did = o.get_device_identification()
        except Exception as ex:
            rows.append((nm(o), "", "NO-ID", str(ex)[:60]))
        if did is not None:
            key = "(%s, %s, %s)" % (did.type, did.id, did.version)
            try:
                d = device_repository.get_device(did)
                if d is None:
                    rows.append((nm(o), key, "MISSING", "not in repository"))
                else:
                    try:
                        rn = d.device_info.name
                        rn = (rn.encode("utf-8", "replace")
                              if isinstance(rn, unicode) else str(rn))
                    except Exception:
                        rn = "?"
                    rows.append((nm(o), key, "ok", rn))
            except Exception as ex:
                rows.append((nm(o), key, "MISSING", str(ex)[:70]))
    try:
        for c in o.get_children():
            walk(c)
    except Exception:
        pass


for top in proj.get_children():
    walk(top)

ok = [r for r in rows if r[2] == "ok"]
slots = [r for r in rows if r[2] != "ok" and r[1] == EMPTY_SLOT]
bad = [r for r in rows if r[2] != "ok" and r[1] != EMPTY_SLOT]

p("")
p("device nodes : %d" % len(rows))
p("resolved     : %d" % len(ok))
p("empty slots  : %d   (unpopulated module slots, not a problem)" % len(slots))
p("MISSING      : %d" % len(bad))

if bad:
    p("")
    p("== descriptions NOT installed on this machine ==")
    grouped = {}
    for n, k, st, detail in bad:
        if k not in grouped:
            grouped[k] = [n, detail, 0]
        grouped[k][2] += 1
    for k in sorted(grouped):
        n, detail, c = grouped[k]
        p("  %-46s x%-3d e.g. %s" % (k, c, n[:30]))
    p("")
    p("Fix: install the missing descriptions, then re-run this job.")
    p("  - Tools > Device Repository... > Install  (for ESI / devdesc files)")
    p("  - Tools > CODESYS Installer...            (for vendor add-ons)")
    p("  - or extract a project archive that bundles device descriptions")
else:
    p("")
    p("All device descriptions resolve. Project is buildable on this machine.")

p("")
p("== resolved descriptions (unique) ==")
u = {}
for n, k, st, rn in ok:
    u[(k, rn)] = u.get((k, rn), 0) + 1
for k, rn in sorted(u):
    p("  %-46s x%-3d %s" % (k, u[(k, rn)], rn[:44]))

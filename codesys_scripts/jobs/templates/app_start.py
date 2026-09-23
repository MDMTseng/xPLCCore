# -*- coding: ascii -*-
# Start the application that is already downloaded on the controller.
#
#   rpc.py exec --readonly --file jobs/templates/app_start.py
#
# THIS TOUCHES A REAL MACHINE. It calls start() on the running
# controller. It does not download, reset, force anything, or issue any
# motion command -- the FSM comes up in UnInited exactly as it does after
# a manual start.
#
# login(OnlineChangeOption.Never, False):
#   Never (0) so a code difference is refused rather than silently
#   pushed into the machine as an online change. delete_foreign_apps
#   False so no other application is removed.
#
# Precondition this script checks and refuses to proceed without: every
# delta arm drive must be virtual in the device tree. EAXIS_A (drive 6)
# and reelpullmotor (drive 8) are real and will be initialised by
# SoftMotion on start; that is expected on this rig.

import time
import traceback

DELTA_AXES = ("EAxis0", "EAxis1", "EAxis2")


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


proj = projects.primary

# ---- precondition: delta arms virtual --------------------------------
virt = {}


def scan(o):
    try:
        if o.is_device and t(o.get_name()) in DELTA_AXES:
            for c in o.connectors:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    try:
                        if t(prm.name) == "bVirtual":
                            virt[t(o.get_name())] = t(prm.value)
                    except Exception:
                        pass
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)

p("precondition -- delta arms virtual?")
ok = True
for a in DELTA_AXES:
    v = virt.get(a, "<not found>")
    good = v.upper() in ("TRUE", "1")
    if not good:
        ok = False
    p("  %-10s bVirtual = %-12s %s" % (a, v, "ok" if good else "** NOT VIRTUAL **"))

if not ok:
    p("")
    p("ABORTED: a delta arm is not virtual. Not starting.")
    raise SystemExit(1)

# ---- start -----------------------------------------------------------
apps = list(proj.find("Application", True) or [])
oapp = online.create_online_application(apps[0])

p("")
p("logging in (Never, no online change)...")
oapp.login(OnlineChangeOption.Never, False)
try:
    p("state before: %s" % t(oapp.application_state))
    if t(oapp.application_state) == "run":
        p("already running -- nothing to do")
    else:
        p("calling start()...")
        oapp.start()
        for _ in range(10):
            time.sleep(0.5)
            if t(oapp.application_state) == "run":
                break
        p("state after : %s" % t(oapp.application_state))
finally:
    try:
        oapp.logout()
        p("logged out")
    except Exception:
        p(traceback.format_exc()[:300])

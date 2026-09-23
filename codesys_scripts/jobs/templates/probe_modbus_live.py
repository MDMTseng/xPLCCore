# -*- coding: ascii -*-
# Log in READ-ONLY and ask the running controller whether the Modbus
# master is alive.
#
#   rpc.py exec --readonly --file jobs/templates/probe_modbus_live.py
#
# Safety, deliberately:
#
#   login(OnlineChangeOption.Never, False)
#
#   Never (0) -- do NOT perform an online change. Try (1) would push the
#   project's code into the running machine if the two differ, which is
#   a modification, not an observation. Force (2) is worse.
#   delete_foreign_apps=False -- True deletes other applications off the
#   controller.
#
# Nothing here writes, forces, starts, stops or resets. logout runs in a
# finally so a failed read cannot leave the session open and block the
# next login.
#
# Why: the PLC log shows zero Modbus activity across 668 entries, after
# a fresh download and cold reset, while the serial runtime components
# are loaded and no port error is reported. That pattern says the master
# is generated but never executed. The diagnostic structure on the live
# controller settles it.

import traceback

CANDIDATES = [
    # The generated Modbus instances, per the device's
    # "Modbus Client Instance" parameter.
    "ModbusSlaveComPort_Diag",
    "ModbusSlaveComPort",
    "Application.ModbusSlaveComPort_Diag",
    "Application.ModbusSlaveComPort",
    # Controls: these are known to exist, so a failure here means the
    # read path is wrong rather than the symbol being absent.
    "GVL.AxisSimMask",
    "GVL.AxisSimMaskApplied",
]


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
apps = list(proj.find("Application", True) or [])
if not apps:
    p("no Application object")
    raise SystemExit(1)

oapp = online.create_online_application(apps[0])

p("logging in (OnlineChangeOption.Never, delete_foreign_apps=False)...")
try:
    oapp.login(OnlineChangeOption.Never, False)
except Exception:
    p("login failed:")
    p(traceback.format_exc()[:900])
    raise SystemExit(1)

try:
    p("logged in: %s" % oapp.is_logged_in)
    try:
        p("application_state: %s" % t(oapp.application_state))
    except Exception as ex:
        p("application_state: <%s>" % t(ex)[:70])
    try:
        p("operation_state  : %s" % t(oapp.operation_state))
    except Exception as ex:
        p("operation_state  : <%s>" % t(ex)[:70])

    p("")
    p("=" * 66)
    p("SYMBOL READS")
    p("=" * 66)
    for sym in CANDIDATES:
        try:
            val = oapp.read_value(sym)
            p("  ok    %-38s = %s" % (sym, t(val)[:70]))
        except Exception as ex:
            p("  fail  %-38s %s" % (sym, t(ex).replace(chr(10), " ")[:60]))
finally:
    try:
        oapp.logout()
        p("")
        p("logged out")
    except Exception:
        p("logout failed:")
        p(traceback.format_exc()[:400])

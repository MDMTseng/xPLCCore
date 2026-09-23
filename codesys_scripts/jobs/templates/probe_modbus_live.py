# -*- coding: ascii -*-
# Log in READ-ONLY and ask the running controller whether the Modbus
# master is alive.
#
#   rpc.py exec --readonly --file jobs/templates/probe_modbus_live.py
#
# Safety, deliberately:
#
#   login(OnlineChangeOption.Keep, False)
#
# OnlineChangeOption, the trap:
#
#   Keep  (3) attach to whatever is already on the controller. The only
#             option that leaves a running application running.
#   Never (0) do NOT online-change -- so it does a FULL DOWNLOAD instead,
#             which stops the application. The name reads like "change
#             nothing"; it means "never change it incrementally".
#   Try   (1) online-change if the code differs.
#   Force (2) online-change regardless.
#
# Verified on this rig: login(Never, False) on a running application
# stopped it every time, with application_state reading "stop" before
# logout was even called. login(Keep, False) left it running and read
# symbols fine. Anything that calls itself read-only must use Keep.
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

p("logging in (OnlineChangeOption.Keep, delete_foreign_apps=False)...")
try:
    oapp.login(OnlineChangeOption.Keep, False)
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

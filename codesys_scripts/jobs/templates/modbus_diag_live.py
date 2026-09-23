# -*- coding: ascii -*-
# Read the Modbus devices' ONLINE diagnostic values from the running
# controller.
#
#   rpc.py exec --readonly --file jobs/templates/modbus_diag_live.py
#
# Device parameters carry two values: the configured one (prm.value) and
# the live one (prm.read_online_value()). The live side is what settles
# whether a serial master is actually running, and it needs no extra
# hardware -- no sniffer, no second adapter, nothing wired into a spare
# input.
#
# How to read the result:
#
#   ComState says the port is not open        -> the port mapping is wrong
#   Error counter stays 0 and nothing moves   -> the master never executes
#   Error counter climbs, ErrorCode = timeout -> the port works; the
#                                                problem is wiring, A/B
#                                                polarity, termination or
#                                                the feeder's own settings
#
# Read-only: login uses OnlineChangeOption.Keep so a code difference is
# refused rather than pushed into the machine, and nothing is written,
# forced, started or stopped.

import time
import traceback

SAMPLES = 3
GAP = 2.0


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


proj = projects.primary

SERIAL_TYPES = (90, 91, 92)
devices = []


def scan(o):
    try:
        if o.is_device and o.get_device_identification().type in SERIAL_TYPES:
            devices.append(o)
    except Exception:
        pass
    try:
        for c in o.get_children():
            scan(c)
    except Exception:
        pass


for top in proj.get_children():
    scan(top)

if not devices:
    p("no Modbus devices found")
    raise SystemExit(1)

apps = list(proj.find("Application", True) or [])
oapp = online.create_online_application(apps[0])

p("logging in (Keep, read-only)...")
oapp.login(OnlineChangeOption.Keep, False)

try:
    p("application_state: %s" % t(oapp.application_state))
    p("")

    for sample in range(SAMPLES):
        p("=" * 68)
        p("sample %d of %d" % (sample + 1, SAMPLES))
        p("=" * 68)
        for dev in devices:
            printed = False
            try:
                cons = list(dev.connectors)
            except Exception:
                cons = []
            for c in cons:
                try:
                    prms = list(c.host_parameters)
                except Exception:
                    continue
                for prm in prms:
                    try:
                        can = prm.can_access_online
                    except Exception:
                        can = False
                    if not can:
                        continue
                    name = t(getattr(prm, "name", "?"))
                    try:
                        # read_online_value(nTimeOut) -- milliseconds.
                        live = prm.read_online_value(2000)
                    except Exception as ex:
                        live = "<%s>" % t(ex).replace(chr(10), " ")[:44]
                    if not printed:
                        p("")
                        p("  %s" % nm(dev))
                        printed = True
                    p("     %-30s = %s" % (name[:30], t(live)[:80]))
        if sample < SAMPLES - 1:
            time.sleep(GAP)
            p("")

    p("")
    p("If a counter is identical across all samples, nothing is moving.")
finally:
    try:
        oapp.logout()
        p("")
        p("logged out")
    except Exception:
        p(traceback.format_exc()[:300])

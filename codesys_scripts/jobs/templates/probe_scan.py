# -*- coding: ascii -*-
# Check whether PLC_PRG is actually being scanned by inspecting
# R_TRIG internal state after forcing the trigger.
import time
proj = projects.primary
app_obj = None
for app in list(proj.find("Application", True) or []):
    app_obj = app; break
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
try:
    oapp.unforce_all_values()
    time.sleep(0.1)

    print("bRun input  :", oapp.read_value("GVL.fbMsgPackTests.bRun"))
    print("trigger     :", oapp.read_value("GVL.bRunMsgPackTests"))

    oapp.set_prepared_value("GVL.bRunMsgPackTests", "TRUE")
    oapp.force_prepared_values()
    time.sleep(0.3)

    print("--- after forcing TRUE ---")
    print("trigger     :", oapp.read_value("GVL.bRunMsgPackTests"))
    print("bRun input  :", oapp.read_value("GVL.fbMsgPackTests.bRun"))
    # R_TRIG internals: typical names are M (memory) and Q (output)
    for name in ["rt.M", "rt.Q", "rt.CLK", "rt.CLK_OLD", "rt.bMemory"]:
        try:
            print("  fb.{} = {}".format(name,
                oapp.read_value("GVL.fbMsgPackTests." + name)))
        except Exception as ex:
            print("  fb.{} -> {}".format(name, str(ex)[:80]))

    # Is there a cycle-count or scan-count in IoConfig/Task?
    try:
        print("cycle_count:", oapp.read_value("PLC_PRG.ccc"))
    except Exception as ex:
        print("ccc:", ex)

    oapp.unforce_all_values()
finally:
    oapp.logout()

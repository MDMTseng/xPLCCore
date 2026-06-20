"""Cycle bVirtualMotorsMode_Request FALSE->TRUE then poll until
bVirtualMotorsMode actually flips TRUE. The TON gate behind Request
latches its Q output across TTL expiry, so a direct FALSE->TRUE bounce
is needed to re-arm (see memory: virtual_motors_gate_ton_reset)."""
import time as _t
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "FALSE")
oapp.force_prepared_values()
_t.sleep(0.5)
oapp.unforce_all_values()
_t.sleep(0.3)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request", "TRUE")
oapp.force_prepared_values()

for i in range(40):
    v = str(oapp.read_value("GVL.bVirtualMotorsMode"))
    print("  t=%.1f bVirtualMotorsMode=%s" % (i*0.25, v))
    if v.endswith("TRUE"):
        print("OK, virtual mode active")
        break
    _t.sleep(0.25)
else:
    print("FAIL: virtual mode did not engage")

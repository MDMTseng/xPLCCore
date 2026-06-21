"""Force ConveyorPulseRaw back to 0 before the next viz run so the
plot starts at a clean baseline (no leftover offset from prior tests)."""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
import time
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.set_prepared_value("GVL.ConveyorPulseRaw","0")
oapp.force_prepared_values()
time.sleep(0.1)
oapp.unforce_all_values()
print("ConveyorPulseRaw =", oapp.read_value("GVL.ConveyorPulseRaw"))

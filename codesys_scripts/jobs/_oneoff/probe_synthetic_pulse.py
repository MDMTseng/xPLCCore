"""Quick sanity check: enable synthetic pulse, watch ConveyorPulseRaw
advance + wrap at 65535. Disable when done so other tests aren't
polluted."""
import time as _t
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

def rd(name):
    raw = str(oapp.read_value(name))
    return int(raw.split("#",1)[1].rstrip(")")) if "#" in raw else int(raw)

print("baseline ConveyorPulseRaw =", rd("GVL.ConveyorPulseRaw"))

# Force pulse to a known starting point near the wrap to also verify
# the AND 16#FFFF wrap behavior in one pass.
oapp.set_prepared_value("GVL.ConveyorPulseRaw", "65530")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep", "100")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "TRUE")
oapp.force_prepared_values()
_t.sleep(0.05)
oapp.unforce_all_values()  # release the pulse + step + enable forces
# After unforce, the runtime continues to use the LAST forced enable
# state? No -- unforce restores the GVL default (FALSE). So we re-force
# only the Enable flag to keep it running while we watch.
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep", "100")
oapp.force_prepared_values()

for i in range(6):
    _t.sleep(0.05)
    print("  t=%.2f ConveyorPulseRaw =" % (i*0.05), rd("GVL.ConveyorPulseRaw"))

oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "FALSE")
oapp.force_prepared_values()
_t.sleep(0.1)
oapp.unforce_all_values()
print("disabled; final ConveyorPulseRaw =", rd("GVL.ConveyorPulseRaw"))

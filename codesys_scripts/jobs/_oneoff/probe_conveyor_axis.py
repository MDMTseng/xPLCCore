"""Verify ConveyorPulseRaw -> ConveyorEncoderAxis.fActPosition bridge."""
import time as _t
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

def rd(name):
    raw = str(oapp.read_value(name))
    if "#" in raw:
        try: return float(raw.split("#",1)[1].rstrip(")"))
        except: return raw
    try: return float(raw)
    except: return raw

print("Coord1PulseToMm =", rd("AxisGroupSM.Coord1PulseToMm"),
      "(my edits present?)")

print("\nbaseline:")
print("  ConveyorPulseRaw   =", rd("GVL.ConveyorPulseRaw"))
print("  axis fSetPosition  =", rd("ConveyorEncoderAxis.fSetPosition"))
print("  axis fActPosition  =", rd("ConveyorEncoderAxis.fActPosition"))
print("  axis driveError    =", rd("ConveyorEncoderAxis.uiDriveInterfaceError"))

print("\nforce pulse=12345 -> expect fSet ~= 123.45:")
oapp.set_prepared_value("GVL.ConveyorPulseRaw", "12345")
oapp.force_prepared_values()
_t.sleep(0.2)
print("  ConveyorPulseRaw   =", rd("GVL.ConveyorPulseRaw"))
print("  axis fSetPosition  =", rd("ConveyorEncoderAxis.fSetPosition"))
print("  axis fActPosition  =", rd("ConveyorEncoderAxis.fActPosition"))

print("\nenable synthetic step=200, watch advance:")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep", "200")
oapp.force_prepared_values()
_t.sleep(0.05)
oapp.unforce_all_values()
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep", "200")
oapp.force_prepared_values()
for i in range(5):
    _t.sleep(0.1)
    print("  t=%.1f  pulse=%5d  fSet=%8.3f  fAct=%8.3f" %
          (i*0.1,
           int(rd("GVL.ConveyorPulseRaw")),
           rd("ConveyorEncoderAxis.fSetPosition"),
           rd("ConveyorEncoderAxis.fActPosition")))

print("\ncleanup:")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable", "FALSE")
oapp.force_prepared_values()
_t.sleep(0.1)
oapp.unforce_all_values()
print("done.")

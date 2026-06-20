"""Phase 1 verification: force ConveyorPulseRaw + bind state, read back
OriginNow drift via SYS/GET_COORD1_DEBUG. Confirms the per-scan recompute
in AxisGroupSM is wired correctly without yet needing the COORD1_BIND
SYS handler (Phase 2).

Forces:
  Coord1RefPulse = 1000
  Coord1RefXyz   = [396, 87, 0]
  Coord1Scale    = [10.0, 0.0, 0.0]   # 10 pulses/mm in X; Y/Z locked
  Coord1Bound    = TRUE
  ConveyorPulseRaw = 1500            # -> Coord1OriginNow[0] = 396 + (1500-1000)/10 = 446

Expected debug reply: origin_x = 446.0, origin_y = 87.0, origin_z = 0.0.
"""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

forces = [
    ("GVL.Coord1RefPulse",     "1000"),
    ("GVL.Coord1RefXyz[0]",    "LREAL#396.0"),
    ("GVL.Coord1RefXyz[1]",    "LREAL#87.0"),
    ("GVL.Coord1RefXyz[2]",    "LREAL#0.0"),
    ("GVL.Coord1Scale[0]",     "LREAL#10.0"),
    ("GVL.Coord1Scale[1]",     "LREAL#0.0"),
    ("GVL.Coord1Scale[2]",     "LREAL#0.0"),
    ("GVL.Coord1Bound",        "TRUE"),
    ("GVL.ConveyorPulseRaw",   "1500"),
]
for sym, val in forces:
    oapp.set_prepared_value(sym, val)
oapp.force_prepared_values()
print("forced; waiting one scan for compute...")

# Read back via the cyclic-side computed mirror
for sym in ["GVL.ConveyorPulseRaw", "GVL.Coord1Bound",
            "GVL.Coord1OriginNow[0]", "GVL.Coord1OriginNow[1]", "GVL.Coord1OriginNow[2]"]:
    v = oapp.read_value(sym)
    print(sym, "=", v)

# Bump pulse to verify drift
oapp.set_prepared_value("GVL.ConveyorPulseRaw", "2500")
oapp.force_prepared_values()
print("\nbumped pulse to 2500 -> expect origin_x = 396 + (2500-1000)/10 = 546.0")
import time as _t; _t.sleep(0.2)
print("GVL.Coord1OriginNow[0] =", oapp.read_value("GVL.Coord1OriginNow[0]"))
print("GVL.ConveyorPulseRaw =", oapp.read_value("GVL.ConveyorPulseRaw"))

print("\nUnforcing all (clean slate).")
oapp.unforce_all_values()
print("done.")

"""Phase 2 live interp test: drives the cam table that was loaded by
probe_coord1_bind.py Test 4, sweeps ConveyorPulseRaw across the table,
reads back Coord1OriginNow each time. Expects clamp + linear interp.

Table from Test 4:
  master=[1000, 2000, 3000]   x=[0, 100, 300]   y=[0,0,0]   z=[0,0,0]
  ref_xyz=[0,0,0]

Probes:
  pulse=  500 -> clamp first -> origin_x=0   (refxyz + cam[0])
  pulse= 1000 -> exact          origin_x=0
  pulse= 1500 -> halfway        origin_x= 50 (0 + 0.5*(100-0))
  pulse= 2000 -> exact          origin_x=100
  pulse= 2500 -> halfway        origin_x=200 (100 + 0.5*(300-100))
  pulse= 3000 -> exact          origin_x=300
  pulse= 4000 -> clamp last  -> origin_x=300

Run AFTER probe_coord1_bind.py so the table is loaded.
"""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)

cases = [
    (500,  0.0),
    (1000, 0.0),
    (1500, 50.0),
    (2000, 100.0),
    (2500, 200.0),
    (3000, 300.0),
    (4000, 300.0),
]
import time as _t

print("Coord1CamMode =", oapp.read_value("GVL.Coord1CamMode"),
      "(0=Linear, 1=Table)")
print("Coord1CamCount =", oapp.read_value("GVL.Coord1CamCount"))

pass_count = 0
for pulse, expected_x in cases:
    oapp.set_prepared_value("GVL.ConveyorPulseRaw", "%d" % pulse)
    oapp.force_prepared_values()
    _t.sleep(0.1)
    raw = oapp.read_value("GVL.Coord1OriginNow[0]")
    # raw is like 'LREAL#50' -- parse the trailing number
    try:
        s = str(raw)
        num = float(s.split("#", 1)[1].rstrip(")"))
    except Exception:
        num = float(s)
    delta = abs(num - expected_x)
    status = "OK" if delta < 0.01 else "FAIL"
    if delta < 0.01:
        pass_count += 1
    print("  pulse=%d expected=%.1f got=%.4f %s" % (pulse, expected_x, num, status))

print("\n%d / %d cases passed" % (pass_count, len(cases)))
oapp.unforce_all_values()
print("unforced; done.")

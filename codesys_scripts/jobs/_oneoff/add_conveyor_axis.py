"""Add ConveyorEncoderAxis (SMC_VirtualDrive type 1024/FFFF 0001) under
SoftMotion General Axis Pool. Idempotent; safe to re-run."""

proj = projects.primary
pool = list(proj.find("SoftMotion General Axis Pool", True))[0]

if list(proj.find("ConveyorEncoderAxis", True)):
    print("ConveyorEncoderAxis already exists -- nothing to do")
else:
    sib_id = pool.get_children()[0].get_device_identification()
    print("using device_id:", sib_id)
    pool.add("ConveyorEncoderAxis", sib_id)
    print("added")

# Verify
hits = list(proj.find("ConveyorEncoderAxis", True))
if hits:
    n = hits[0]
    di = n.get_device_identification()
    print("verified node:", n.get_name(), "device_id:", di)
    proj.save()
    print("project saved")
else:
    print("FAIL: post-add lookup did not find ConveyorEncoderAxis")

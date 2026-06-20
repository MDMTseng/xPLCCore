"""Direct GVL/AxisGroupSM read of arm/kernel state — bypasses TCP FSM."""
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
    return raw

print("== arm position (TCP via MC_GroupReadActualPosition) ==")
print("  ReadPosition.c.x =", rd("AxisGroupSM.ReadPosition.c.x"))
print("  ReadPosition.c.y =", rd("AxisGroupSM.ReadPosition.c.y"))
print("  ReadPosition.c.z =", rd("AxisGroupSM.ReadPosition.c.z"))

print("\n== group status ==")
print("  Error            =", rd("AxisGroupSM.GroupReadStatusFb.Error"))
print("  GroupErrorStop   =", rd("AxisGroupSM.GroupReadStatusFb.GroupErrorStop"))
print("  ErrorID          =", rd("AxisGroupSM.GroupReadStatusFb.ErrorID"))
print("  LastAcceptedMovementId =", rd("AxisGroupSM.GroupReadStatusFb.LastAcceptedMovementId"))

print("\n== MotionPose (latched G1 target in user frame) ==")
print("  c.X =", rd("AxisGroupSM.MotionPose.c.x"))
print("  c.Y =", rd("AxisGroupSM.MotionPose.c.y"))
print("  c.Z =", rd("AxisGroupSM.MotionPose.c.z"))

print("\n== PCS_1 kernel transform ==")
print("  Pcs1KernelTransform.X =", rd("AxisGroupSM.Pcs1KernelTransform.X"))
print("  Pcs1KernelTransform.Y =", rd("AxisGroupSM.Pcs1KernelTransform.Y"))
print("  Pcs1KernelTransform.Z =", rd("AxisGroupSM.Pcs1KernelTransform.Z"))
print("  ConveyorBeltOrigin.X =", rd("AxisGroupSM.Coord1BeltOriginRef.X"))
print("  ConveyorEncoderAxis.fActPosition =", rd("ConveyorEncoderAxis.fActPosition"))
print("  ConveyorPulseRaw =", rd("GVL.ConveyorPulseRaw"))

print("\n== tcb status ==")
print("  trackConveyorBeltFb.Execute =", rd("AxisGroupSM.trackConveyorBeltFb.Execute"))
print("  trackConveyorBeltFb.Done    =", rd("AxisGroupSM.trackConveyorBeltFb.Done"))
print("  trackConveyorBeltFb.Busy    =", rd("AxisGroupSM.trackConveyorBeltFb.Busy"))
print("  trackConveyorBeltFb.Error   =", rd("AxisGroupSM.trackConveyorBeltFb.Error"))

# -*- coding: ascii -*-
# 1) Print AxisGroupSM declaration (lines 1..40) to find the
#    AxisGroupManager instance name. (Use getattr, not direct attr access.)
# 2) Poll axis state + bError 10x at 250ms to see if anything is flipping.

import time

proj = projects.primary
oapp = online.create_online_application(proj.active_application)

def _text(obj, attr):
    v = getattr(obj, attr, None)
    if v is None:
        return None
    t = getattr(v, "text", None)
    return t

def find(obj, name):
    try:
        if obj.get_name() == name:
            return obj
    except Exception:
        pass
    try:
        for c in obj.get_children():
            r = find(c, name)
            if r is not None:
                return r
    except Exception:
        pass
    return None

asm = None
for top in proj.get_children():
    asm = find(top, "AxisGroupSM")
    if asm is not None:
        break

if asm is None:
    print("!! AxisGroupSM not found")
else:
    print("==== AxisGroupSM declaration (lines 1..40) ====")
    txt = _text(asm, "textual_declaration")
    if not txt:
        # dump attributes to figure out where the text lives
        print("   textual_declaration missing; dir:")
        for a in sorted(x for x in dir(asm) if not x.startswith("_")):
            print("    ", a)
    else:
        for i, line in enumerate(txt.splitlines()[:40], 1):
            print("  {:3d}: {}".format(i, line))

def rv(expr):
    try:
        return oapp.read_value(expr)
    except Exception as ex:
        return "<err:{}>".format(str(ex)[:40])

print("\n==== 10 samples @ 250ms ====")
for i in range(10):
    state  = rv("reelpullmotor.nAxisState")
    berr   = rv("reelpullmotor.bError")
    breg   = rv("reelpullmotor.bRegulatorRealState")
    fact   = rv("reelpullmotor.fActPosition")
    fset   = rv("reelpullmotor.fSetPosition")
    mvx    = rv("AxisGroupSM.reelMoveRelative.Execute")
    mvdn   = rv("AxisGroupSM.reelMoveRelative.Done")
    mverr  = rv("AxisGroupSM.reelMoveRelative.Error")
    mvea   = rv("AxisGroupSM.reelMoveRelative.ErrorID")
    mvact  = rv("AxisGroupSM.reelMoveRelative.Active")
    print("  #{}: state={} bErr={} bReg={} fAct={} fSet={} mv.Ex={} mv.Act={} mv.Err={}/{} mv.Dn={}".format(
        i, state, berr, breg, fact, fset, mvx, mvact, mverr, mvea, mvdn))
    time.sleep(0.25)

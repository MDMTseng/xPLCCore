# -*- coding: ascii -*-
# READ-ONLY. Members of the IDE's online-device and logger interfaces that
# could read the PLC log. Reflection only.

from System import AppDomain


def find_type(name):
    for asm in AppDomain.CurrentDomain.GetAssemblies():
        try:
            t = asm.GetType(name)
        except Exception:
            t = None
        if t is not None:
            return t
    return None


def show(name, filt=None):
    t = find_type(name)
    print("==", name, "found" if t else "NOT FOUND")
    if t is None:
        return
    seen = set()
    stack = [t]
    while stack:
        x = stack.pop()
        if x.FullName in seen:
            continue
        seen.add(x.FullName)
        for m in x.GetMethods():
            if filt is None or any(k in m.Name for k in filt):
                ps = ", ".join("%s %s" % (p.ParameterType.Name, p.Name) for p in m.GetParameters())
                print("   %s.%s(%s) -> %s" % (x.Name, m.Name, ps, m.ReturnType.Name))
        for p in x.GetProperties():
            if filt is None or any(k in p.Name for k in filt):
                print("   %s.[prop] %s : %s" % (x.Name, p.Name, p.PropertyType.Name))
        for i in x.GetInterfaces():
            stack.append(i)


show("_3S.CoDeSys.Core.Online.IOnlineDevice7", ("Log", "log"))
show("_3S.CoDeSys.Core.Online.ILoggerServiceHandler3")
show("_3S.CoDeSys.Core.Online.ILoggerEntries2")

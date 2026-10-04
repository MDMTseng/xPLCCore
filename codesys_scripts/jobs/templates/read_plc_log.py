# -*- coding: ascii -*-
# READ-ONLY. Read the PLC's runtime log (what the IDE shows under Device ->
# Log, e.g. PlcLog) through the IDE's logger service, and write it to
# jobs/plc_log_<logger>.txt, oldest first:
#   timestamp<TAB>severity<TAB>component<TAB>message
# Device-level connection only (no application login). The scripting API
# has no log call; this uses IOnlineDevice3.CreateLoggerServiceHandler via
# the ScriptOnlineDevice's OnlineDevice field (found 2026-10-01).
#
#   PYTHONIOENCODING=utf-8 rpc.py exec --readonly --file jobs/templates/read_plc_log.py

import os
import time
from System import DateTime, Array, UInt32
import clr
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import Application


def wait(ar, what, secs=20):
    # The logger service completes through the IDE's message loop; this job
    # runs on that thread, so End*() alone deadlocks (it hung 10 min on
    # 2026-10-01). Pump the loop until the call completes, or give up.
    t0 = time.time()
    while not ar.IsCompleted:
        Application.DoEvents()
        time.sleep(0.02)
        if time.time() - t0 > secs:
            raise RuntimeError("%s: no answer in %d s" % (what, secs))
    return ar
from System.Reflection import BindingFlags

LOGGER = "PlcLog"
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else ".", "..")


def t(v):
    """ASCII only: the daemon captures stdout through cStringIO and the
    file is written in text mode, and both choked on the micro sign in
    SoftMotion's "... us" messages (UnicodeDecodeError 2026-10-04); the job
    then died and left the previous plc_log_*.txt in place."""
    try:
        if not isinstance(v, unicode):
            v = unicode(v)
        return v.replace(u"\xb5", u"u").encode("ascii", "replace")
    except Exception:
        return "?"


dev = None
for top in projects.primary.get_children():
    if top.is_device:
        dev = top
        break
od = online.create_online_device(dev)
od.connect()
try:
    f = od.GetType().GetField("OnlineDevice", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
    idev = f.GetValue(od)
    h = idev.CreateLoggerServiceHandler()
    names = h.EndGetLoggerNames(wait(h.BeginGetLoggerNames(None, None), "logger names"))
    print("loggers:", ", ".join(t(n) for n in names))
    entries = h.EndGetLoggerEntries(wait(h.BeginGetLoggerEntries(None, None, UInt32(0), DateTime.MinValue, LOGGER), "entries", 60))
    print("entries:", len(entries))
    ids = sorted(set(int(e.CmpID) for e in entries))
    cmp_names = {}
    try:
        arr = Array[UInt32]([UInt32(i) for i in ids])
        ht = h.EndGetComponentName(wait(h.BeginGetComponentName(None, None, arr), "component names"))
        for k in ht.Keys:
            cmp_names[int(k)] = t(ht[k])
    except Exception as ex:
        print("component names: %s" % ex)
    rows = []
    for e in entries:
        rows.append((e.TimeStamp, "%s\t%s\t%s\t%s" % (
            t(e.TimeStamp.ToString("yyyy-MM-dd HH:mm:ss.fff")), t(e.GetSeverity()),
            cmp_names.get(int(e.CmpID), str(e.CmpID)), t(e.Info).replace("\n", " "))))
    rows.sort(key=lambda r: r[0])
    path = r"C:\Users\PC\Documents\workspace\codesys_dev\xPLCCore\codesys_scripts\jobs\plc_log_%s.txt" % LOGGER
    fh = open(path, "w")
    for _, line in rows:
        fh.write(line + "\n")
    fh.close()
    print("written:", path)
    for _, line in rows[-5:]:
        print(line)
finally:
    try:
        od.disconnect()
    except Exception:
        pass

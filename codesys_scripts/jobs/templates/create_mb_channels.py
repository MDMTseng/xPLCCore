# -*- coding: ascii -*-
# Add Modbus channels to a serial Modbus slave, the way the device
# editor's "Modbus Slave Channel" tab does.
#
#   rpc.py exec --file jobs/templates/create_mb_channels.py
#
# Channels are not reachable through the scripting API (ScriptDevice
# exposes no channel list). This drives the editor plugin itself,
# DeviceEditorModbus.plugin, through .NET reflection:
#
#   ObjectMgr.GetObjectToModify(project, device guid)  -> editable device
#   ChannelDataBase.Create(connector)                  -> IChannelData
#   PresetFactory.Create(connector, False)             -> preset factory
#   IChannelData.CreateSlaveChannel(conn, item, presets)
#   ObjectMgr.SetObject(meta, True, None)               -> commit + unlock
#
# These are internal types of CODESYS 3.5.22.30 / DeviceEditorModbus
# 4.6.0.0. A CODESYS update can rename them; the script then fails at
# the lookup, before anything is modified.
#
# Idempotent by channel name: an existing channel with the same name is
# left alone. Builds and prints errors; downloading is a separate step.

import System
from System.Reflection import BindingFlags as BF
from System import Guid

DEVICE = "SmartBowlFeeder"

# Register map: doc/3-subsystems/flex_feeder.md
#   (name, function code, offset, length, trigger, cycle ms)
# FC03 reads the DATA space; FC06 writes the COMMAND space. The feeder
# keeps them separate: the same address means different things per FC.
CYCLIC, RISING_EDGE = 5, 6
CHANNELS = [
    ("rs485_cfg", 3, 0x0039, 1, CYCLIC, 1000),
]

BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
ALL = BF.Public | BF.NonPublic | BF.Instance | BF.Static


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


asm = None
for a in System.AppDomain.CurrentDomain.GetAssemblies():
    if a.GetName().Name == "DeviceEditorModbus.plugin":
        asm = a
if asm is None:
    print("ABORT: DeviceEditorModbus.plugin not loaded")
    raise SystemExit(1)


def typ(name):
    x = asm.GetType(name)
    if x is None:
        print("ABORT: type %s not found" % name)
        raise SystemExit(1)
    return x


ChannelDataBase = typ("_3S.CoDeSys.DeviceEditorModbus.New.ChannelDataBase")
PresetFactory = typ("_3S.CoDeSys.DeviceEditorModbus.New.PresetFactory")
ChannelItem = typ("_3S.CoDeSys.DeviceEditorModbus.ModbusChannelItem")
TriggerType = typ("_3S.CoDeSys.DeviceEditorModbus.TriggerType")
EditStyle = typ("_3S.CoDeSys.DeviceEditorModbus.EditStyle")
APEnv = typ("_3S.CoDeSys.DeviceEditorModbus.APEnvironment")

IChannelData = typ("_3S.CoDeSys.DeviceEditorModbus.New.IChannelData")

objmgr = APEnv.GetProperty("ObjectMgr", ALL).GetValue(None, None)


def call(iface, obj, name, *args):
    # The plugin's types are internal: IronPython cannot bind their
    # members, so every call and field access goes through reflection.
    return iface.GetMethod(name, ALL).Invoke(obj, System.Array[object](list(args)))


def setf(obj, name, value):
    ChannelItem.GetField(name, ALL).SetValue(obj, value)


def getf(obj, name):
    return ChannelItem.GetField(name, ALL).GetValue(obj)


def load(cd, conn):
    return list(call(IChannelData, cd, "LoadSlaveChannels", conn))

proj = projects.primary
found = list(proj.find(DEVICE, True) or [])
if len(found) != 1:
    print("ABORT: %s matched %d objects" % (DEVICE, len(found)))
    raise SystemExit(1)

meta = objmgr.GetObjectToModify(proj.handle, found[0].guid)
committed = False
try:
    dev = meta.Object
    conn = None
    for c in dev.Connectors:
        if int(c.ConnectorId) == 1:
            conn = c
    if conn is None:
        print("ABORT: connector 1 not found")
        raise SystemExit(1)

    cdata = ChannelDataBase.GetMethod("Create", ALL).Invoke(None, System.Array[object]([conn]))
    presets = PresetFactory.GetMethod("Create", ALL).Invoke(None, System.Array[object]([conn, False]))

    existing = load(cdata, conn)
    names = [t(getf(i, "stName")) for i in existing]
    print("existing channels: %s" % (names or "none"))

    added = 0
    for name, fc, offset, length, trig, cycle in CHANNELS:
        if name in names:
            print("  keep %s" % name)
            continue
        item = System.Activator.CreateInstance(ChannelItem, True)
        setf(item, "stName", name)
        setf(item, "usFunctionCode", System.UInt16(fc))
        setf(item, "triggerType", System.Enum.ToObject(TriggerType, trig))
        setf(item, "uiCycleTime", System.UInt32(cycle))
        if fc in (1, 2, 3, 4, 23):
            setf(item, "usReadOffset", System.UInt16(offset))
            setf(item, "usReadLength", System.UInt16(length))
        if fc in (5, 6, 15, 16, 23):
            setf(item, "usWriteOffset", System.UInt16(offset))
            setf(item, "usWriteLength", System.UInt16(length))
        setf(item, "stComment", "")
        setf(item, "editstyle", System.Enum.ToObject(EditStyle, 1))  # NEW
        pid = call(IChannelData, cdata, "GetNextAvailableParamID",
                   call(IChannelData, cdata, "LoadSlaveChannels", conn))
        setf(item, "uiParamID", pid)
        call(IChannelData, cdata, "CreateSlaveChannel", conn, item, presets)
        print("  add  %-12s FC%02d  0x%04X  len %d  %s  param %d"
              % (name, fc, offset, length,
                 "cyclic %d ms" % cycle if trig == CYCLIC else "rising edge",
                 pid))
        added += 1

    objmgr.SetObject(meta, True, None)
    committed = True
    print("committed (%d added)" % added)
finally:
    if not committed:
        try:
            objmgr.SetObject(meta, True, None)
        except Exception:
            pass

# re-read through a fresh read-only view
check = objmgr.GetObjectToRead(proj.handle, found[0].guid).Object
for c in check.Connectors:
    if int(c.ConnectorId) == 1:
        cd2 = ChannelDataBase.GetMethod("Create", ALL).Invoke(None, System.Array[object]([c]))
        for i in load(cd2, c):
            g = lambda n: getf(i, n)
            print("  now: %-12s FC%02d  r 0x%04X/%d  w 0x%04X/%d  trig %s %d ms  param %d"
                  % (t(g("stName")), g("usFunctionCode"), g("usReadOffset"), g("usReadLength"),
                     g("usWriteOffset"), g("usWriteLength"), t(g("triggerType")), g("uiCycleTime"),
                     g("uiParamID")))

system.clear_messages(BUILD)
proj.active_application.generate_code()
errs = 0
for m in system.get_message_objects(BUILD):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)

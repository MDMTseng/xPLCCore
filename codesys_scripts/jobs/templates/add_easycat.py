# -*- coding: ascii -*-
# Add the EasyCAT PRO + ESP32 slave to the EtherCAT bus and map a few of
# its bytes to variables. Offline edit + build; downloading is separate.
#
#   rpc.py exec --file jobs/templates/add_easycat.py
#
# Needs the ESI in the device repository first (import_esi.py).
#
# Position matters: EtherCAT configures slaves in wire order, and add()
# appends as the master's LAST child, i.e. after reel_pull_motor. So the
# cable must go from reel_pull_motor's OUT port to the EasyCAT's IN port.
# A configured slave that is not on the wire (or one on the wire that is
# not configured) keeps the master from reaching OP, which stops every
# axis on the bus -- plug the cable in before downloading.
#
# Firmware (firmware/easycat_esp32): input byte 0 heartbeat, 1 echo of
# output byte 1, 2-10 the AS5600 encoder; output bit 0 drives the LED.
#
# Idempotent: an existing EasyCAT node is reused.

from System import Guid

NAME = "EasyCAT"
DEV_TYPE = 65
# Revision 0x5A01 ("EasyCAT 32+32 rev 1", EasyCAT_V2_0.xml) is the one with
# a <Dc> section. The board's EEPROM already carries the matching config
# (0x0151 = 0x6E: SYNC0 output on, SYNC0 -> AL event), read back by the
# firmware at boot. 0x5A00 (EasyCAT_PRO.xml from the website) has no DC.
DEV_ID = "79A_00DEFEDE00005A01"
DEV_VERSION = "Revision=16#00005A01"

# Distributed clocks, like every other slave on this bus: SYNC0 every
# 1000 us. DCSetting is the index into the ESI's OpMode list:
# 0 "SM_Sync or Async", 1 "DC_Sync" (AssignActivate #x300).
DC_PARAMS = {
    "DC enable": "TRUE",
    "DC sync0 enable": "TRUE",
    "DC sync0 cycletime": "1000",
    "DCSetting": "1",
}

# (channel_type, channel name as the ESI declares it) -> new global
# variable. Input and output channels share names (Byte0..Byte31), so the
# channel type is part of the key.
MAP = {
    ("Output", "Byte0"): "ecat_esp_out0",
    ("Output", "Byte1"): "ecat_esp_out1",
}
# Inputs 0..10 (firmware/easycat_esp32 layout): 0 heartbeat, 1 echo,
# 2-3 encoder raw angle, 4 AS5600 status, 5-8 multi-turn position,
# 9 I2C errors, 10 AGC.
for _i in range(11):
    MAP[("Input", "Byte%d" % _i)] = "ecat_esp_in%d" % _i


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
masters = list(proj.find("EtherCAT_Master_SoftMotion", True) or [])
if len(masters) != 1:
    print("ABORT: EtherCAT master matched %d objects" % len(masters))
    raise SystemExit(1)
master = masters[0]


def child(parent, name):
    for c in parent.get_children():
        if t(c.get_name()) == name:
            return c
    return None


node = child(master, NAME)
if node is None:
    master.add(NAME, DEV_TYPE, DEV_ID, DEV_VERSION)
    node = child(master, NAME)  # add() returns None; re-fetch
    if node is None:
        print("ABORT: add() did not create %s" % NAME)
        raise SystemExit(1)
    print("added %s" % NAME)
else:
    print("%s already present" % NAME)
    di = node.get_device_identification()
    if t(di.id) != DEV_ID:
        node.update(DEV_TYPE, DEV_ID, DEV_VERSION)  # keeps the I/O mappings
        node = child(master, NAME)
        print("updated %s -> %s" % (t(di.id), DEV_ID))

print("bus order: %s" % ", ".join(t(c.get_name()) for c in master.get_children()))


mapped = 0
for c in node.connectors:
    for prm in c.host_parameters:
        try:
            if not prm.is_mappable_io:
                continue
            key = (t(prm.channel_type), t(prm.name))
        except Exception:
            continue
        var = MAP.get(key)
        if var is None:
            continue
        if t(prm.io_mapping.variable) != var:
            prm.io_mapping.variable = var
        print("  map %-6s %-6s -> %s" % (key[0], key[1], var))
        mapped += 1
if mapped != len(MAP):
    print("WARNING: mapped %d of %d channels" % (mapped, len(MAP)))

# The project leaves "Always update variables" Disabled everywhere: mapped
# variables that no task references are then never copied to or from the
# I/O image. The slave reaches OP and exchanges frames, but the variables
# stay 0 and writes never reach the wire. The test variables above are
# used by no program, so update them from the bus cycle task.
di = node.driver_info
mode = type(di.always_update_variables)
di.always_update_variables = getattr(mode, "OnlyIfUnused")
print("always update variables: %s" % node.driver_info.always_update_variables)

for c in node.connectors:
    for prm in c.host_parameters:
        want = DC_PARAMS.get(t(prm.name))
        if want is not None and t(prm.value) != want:
            prm.value = want
for c in node.connectors:
    for prm in c.host_parameters:
        if t(prm.name) in DC_PARAMS:
            print("  %-20s = %s" % (t(prm.name), t(prm.value)))

B = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
system.clear_messages(B)
proj.active_application.generate_code()
errs = [m for m in system.get_message_objects(B) if "error" in str(m.severity).lower()]
for m in errs:
    print("[ERR] %s" % t(m.text))
print("build errors: %d" % len(errs))

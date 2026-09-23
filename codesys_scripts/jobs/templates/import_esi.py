# -*- coding: ascii -*-
# Install an EtherCAT ESI file into the local device repository.
#
#   rpc.py exec --readonly --file jobs/templates/import_esi.py
#
# Edit ESI_PATH / MATCH below (the daemon's exec takes no arguments).
#
# device_repository.import_device needs the converter factory for the
# file format. For ESI it is DeviceEditorEthercat.plugin's
# EthercatDeviceDescriptionConverterFactory, found by reflecting over the
# loaded assemblies for types carrying a TypeGuid:
#
#   3992c588-7bdb-4a7c-908d-f444808d8cd2   EtherCAT ESI (.xml)
#
# Touches the device repository only, not the project -- hence --readonly.
# Idempotent: skips the import if a device matching MATCH already exists.

import System

ESI_PATH = r"C:\Users\PC\Documents\workspace\codesys_dev\xPLCCore\firmware\easycat_esp32\esi\EasyCAT_PRO.xml"
MATCH = "00DEFEDE"   # product code; ESI names are often generic ("Generic 32+32 bytes")
ETHERCAT_CONVERTER = System.Guid("3992c588-7bdb-4a7c-908d-f444808d8cd2")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


def matching():
    out = []
    for d in device_repository.get_all_devices():
        di = d.device_id
        try:
            name = t(d.device_info.name)
            vendor = t(d.device_info.vendor)
        except Exception:
            continue
        if MATCH.lower() in ("%s|%s|%s" % (name, vendor, t(di.id))).lower():
            out.append("%s  (type %s, id %s, version %s)" % (name, di.type, t(di.id), t(di.version)))
    return out


before = matching()
if before:
    print("already installed:")
    for m in before:
        print("  " + m)
else:
    source = device_repository.sources[0]
    print("importing %s into '%s'" % (ESI_PATH, t(source.name)))
    device_repository.import_device(ESI_PATH, source, ETHERCAT_CONVERTER, True)
    after = matching()
    print("installed:" if after else "import returned, but no matching device found")
    for m in after:
        print("  " + m)

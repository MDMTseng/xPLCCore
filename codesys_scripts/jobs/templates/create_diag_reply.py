# -*- coding: ascii -*-
# Create PRG_DiagReply: the read-only diagnostic SYS commands (EC_STATS,
# DWELL, SETTLE, DEM_*, ESP_*, FB_STATS, TASK_STATS, IO_STATE, VERSION,
# GET_DIAG) answered in the Comm task. Called by TCP_MSGPAK_Server, so it is
# NOT added to a task. Source of truth is
# codesys_code/Application/PRG_DiagReply.st (read from disk here); after
# creation import_all.py keeps the POU in sync with that file.
#
#   rpc.py exec --file jobs/templates/create_diag_reply.py
#
# Edits the project only (no PLC contact). Idempotent. Builds; downloading
# is a separate step.
import os
from System import Guid

NAME = "PRG_DiagReply"
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")
IMPL_MARKER = "(* =========== IMPLEMENTATION =========== *)"
SRC = os.path.join(config.source_root(), "Application", NAME + ".st")


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


f = open(SRC, "rb")
try:
    text = f.read().decode("utf-8", "replace")
finally:
    f.close()
idx = text.index(IMPL_MARKER)
DECL = text[:idx].rstrip("\n")
IMPL = text[idx + len(IMPL_MARKER):].lstrip("\n")

proj = projects.primary
app = proj.active_application
pou = None
for o in app.get_children():
    try:
        if t(o.get_name()) == NAME:
            pou = o
    except Exception:
        pass
if pou is None:
    pou = app.create_pou(NAME, PouType.Program)
    print("created %s" % NAME)
else:
    print("%s exists, updating text" % NAME)
pou.textual_declaration.replace(DECL)
pou.textual_implementation.replace(IMPL)

system.clear_messages(BUILD)
app.generate_code()
errs = 0
for m in system.get_message_objects(BUILD):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)

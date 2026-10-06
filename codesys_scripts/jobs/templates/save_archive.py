# -*- coding: ascii -*-
# Write a CODESYS project archive of the open project, with the devices and
# libraries it references, to codesys_project/PackerX.projectarchive (Git
# LFS). A new PC opens this archive to get the project AND the device
# descriptions (ASDA-B3, QEC, EC0808DN, EasyCAT) and libraries (SM3 4.18...)
# it needs (doc/4-dev/new_pc_setup.md).
#
#   rpc.py exec --readonly --label save_archive --file jobs/templates/save_archive.py
#
# Set OUT above the code to write elsewhere. Read-only: no PLC contact.
import time
import os

try:
    OUT
except NameError:
    # source_root() is <repo>/codesys_code
    OUT = os.path.join(os.path.dirname(config.source_root()), "codesys_project", "PackerX.projectarchive")

proj = projects.primary
print("project:", proj.path)
t0 = time.time()
proj.save_archive(OUT)
print("archive written in %.1fs, %d bytes: %s" % (time.time() - t0, os.path.getsize(OUT), OUT))

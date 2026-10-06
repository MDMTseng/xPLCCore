# CODESYS project files (Git LFS)

- `PackerX.projectarchive` -- the machine's project with every device
  description and library it references (CODESYS 3.5.22.30, SoftMotion
  4.18). Extract it on a new PC (doc/4-dev/new_pc_setup.md step 3).
  Regenerate after changing the device tree, tasks or libraries:
  `python codesys_scripts/rpc.py exec --readonly --file jobs/templates/save_archive.py`.
- `PackerX_sim.project` -- the local soft-PLC sim copy (Control Win V3 x64,
  all axes virtual; step 6).

The ST sources of truth are in `codesys_code/`; these files carry what is
not text (device tree, tasks, libraries). Snapshot taken 2026-10-06 12:3x
(the program on the machine since the 08:34 deploy).

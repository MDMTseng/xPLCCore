# Tools

Host-side scripts. Most talk to the PLC through the standalone UI's
harness link (start the UI with `XPLC_HARNESS=1 node standalone/run.cjs`)
and share `machine.py`:
- the link, with a reconnect;
- the FSM;
- the delta's real / virtual mode;
- the safety checks:
  - `--owner-ok` before the real delta moves;
  - the delta powered off before a download (`safe_install`);
  - SyncOffset <= 50;
  - save the PLC log before retrying.

- **moves axes**: needs the owner's OK when the delta is real.
- **downloads**: goes through `machine.safe_install`.
- **writes PLC vars**: through the CODESYS daemon.

Regenerate this file with `python tools/gen_script_index.py`.

| Tool | Touches | What it does |
|---|---|---|
| `a_axis_test.py` | moves axes, writes PLC vars | The group's A axis on the machine (open-loop stepper on the QEC driver's second axis): rotate it through the axis group and check the commanded position, the move times and the peak speed / acceleration against the axis  |
| `a_blend_stress.py` | moves axes | Queued, blended G1s that turn A, over and over: the move pattern the real PLC hung on right after SM3 4.20 was installed (2026-09-29, 5 queued G1s at Cor 45 with a different A each). |
| `a_limit_step.py` | moves axes | One step of the A limit ladder on the machine (the Motors page's test from the command line, for a session where the operator reports the mark). |
| `a_speed_compare.py` | moves axes | Motion time per part with and without A rotations, on the machine. |
| `arrival_phases.py` | moves axes | Does motion change the frame timing? |
| `asda_scope.py` | read-only | Find stale-target events in ASDA-Soft scope recordings (B3-E, 8 kHz). |
| `comm_profile.py` | read-only | Comm task profile: where the Comm task's time goes (EC_STATS cs/cb/co/ cscan/ctask/cout/cout500 from TCP_MSGPAK_Server / PRG_EcatEspHttp stamps) next to TASK_STATS for all tasks. |
| `dc_sweep.py` | moves axes, downloads | Sweep a DC timing setting and measure the ASDA stale-target rate per level. |
| `dem_events.py` | read-only | Compare the timing of the three delta drives' stale-target bursts. |
| `direct_test.py` | moves axes, writes PLC vars | Direct drive test without SoftMotion: EAxis0's controlword and target position are written by PLC code (SYS DIRECT, PRG_EventLog) while SoftMotion holds EAxis0 as a virtual axis. |
| `drive_param.py` | writes PLC vars | Read or write ASDA-B3 drive parameters over EtherCAT SDO (PLC SYS DRV_SDO / DRV_SDO_RESULT). |
| `ec_stress.py` | moves axes, writes PLC vars | EtherCAT task timing under load (owner, 2026-09-29: DC statistics show the odd late cycle while the machine runs). |
| `filter_compare.py` | moves axes | One measurement for comparing drive filter settings (e.g. |
| `fly_leftover_test.py` | moves axes | Fly events a run leaves behind: the default TTL and SYS FLUSH. |
| `gen_script_index.py` | writes these README files | Regenerate the script indexes from each script's own header: codesys_scripts/jobs/templates/README.md and tools/README.md. |
| `host_crash_test.py` | moves axes | The host PC dies without closing its PLC connection (a blue screen): can it connect again after it comes back? |
| `host_gone_test.py` | moves axes | What the PLC does with a host that goes away (resource cleanup). |
| `intewell_watch.py` | read-only | Log the Intewell RTOS task table (and CPU use) of the PLC over its telnet shell, for post-mortem when the PLC hangs. |
| `joint_bench.py` | moves axes | Delta joint bench steps through the UI's link (the Motors page must be open in the standalone UI with XPLC_HARNESS=1; its Delta joints panel shows the same state live, with a STOP). |
| `limit_test.py` | moves axes, writes PLC vars | Do the axis limits hold when the path asks for far more? |
| `machine.py` | shared library (machine access, safety checks) | Shared machine access for the tools: the PLC through the standalone UI's harness link, the FSM, the delta's real/virtual mode, and the safety checks every tool must make the same way. |
| `p3009_sweep.py` | moves axes, downloads | Sweep the ASDA drives' P3.009 (communication synchronization) and measure the stale-target rate at each setting. |
| `plc_direct.py` | library (direct msgpack client, port 8125) | Talk to the PLC's msgpack server (port 8125) directly, without the UI. |
| `pulse_test.py` | moves axes, writes PLC vars | Constant-pulse test for one delta drive, for the ASDA stale-target issue (doc_review/asda_stale_target_2026-09-30.md). |
| `reel_real_test.py` | moves axes, writes PLC vars | Reel axis on the machine: power it, move it, check the cell count and the odometer against the encoder, and the tracker's stop detection on a real servo (a virtual axis has no standstill jitter). |
| `safe_install.py` | downloads | Download the project to the PLC with every safety step (machine.safe_install): 1. |
| `square_dip.py` | moves axes | Square (or line) paths for the delta through the UI's link (standalone UI with XPLC_HARNESS=1): up to Z0, then the corners of a square (+-HALF mm in X and Y at Z0) with a dip to Z DIP at each corner, back to X0 Y0 Z0. |
| `stale_suite.py` | moves axes, downloads | After-recovery test suite for the ASDA stale-target investigation (doc_review/asda_stale_target_2026-09-30.md, sections 7c and 8). |
| `sync_offset_sweep.py` | moves axes, downloads | SyncOffset sweep with the drive-demand metric (ASDA stale-target investigation, doc_review/asda_stale_target_2026-09-30.md section 7c). |
| `sync_shift_sweep.py` | moves axes, downloads | Sweep the ASDA drives' "DC sync0 shift time" and measure the stale-target rate at each level (the drives' SYNC0 fires this much later than the other slaves'; the frame then has more time before the drives' SYNC0). |

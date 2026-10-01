# CODESYS job templates

Scripts run inside the CODESYS IDE by the daemon:
`python codesys_scripts/rpc.py exec [--readonly] --file jobs/templates/<name>.py`
(add `PYTHONIOENCODING=utf-8` when the output may hold non-ASCII).

- **read-only**: inspects the project or the PLC, changes nothing.
- **edits project**: changes and saves the project offline. The PLC keeps
  the old code until a deploy (`rpc.py push` for code, `rpc.py install
  --on-site` for device / task configuration). Deploy with the delta
  powered off (`tools/machine.py safe_install`).
- **PLC session**: logs in, downloads, starts or stops.
- **resets PLC**: resets it.

One-off probes live in `_archive/`. Regenerate this file with
`python tools/gen_script_index.py`.

| Job | Touches | What it does |
|---|---|---|
| `add_easycat.py` | edits project, PLC session | Add the EasyCAT PRO + ESP32 slave to the EtherCAT bus and map a few of its bytes to variables. |
| `add_lib_cmpiectask.py` | edits project | Add the CmpIecTask system library to the Application's Library Manager (task statistics for SYS TASK_STATS: IecTaskGetFirst/Next, IecTaskGetInfo3, IecTaskResetStatistics). |
| `app_start.py` | read-only, PLC session | Start the application that is already downloaded on the controller. |
| `build.py` | ? | build job -- reuses the already-open project, generates code for every Application, and dumps Build-category messages. |
| `check_bus_tasks.py` | read-only | Which task drives each fieldbus master's IO? |
| `check_devices.py` | read-only | Run with: rpc.py exec --readonly --file jobs/templates/check_devices.py For every device node in the open project, check whether its device description actually exists in THIS machine's repository. |
| `check_status.py` | PLC session | Log in to the application and print a few TCP server / FSM status variables. |
| `cold_reset.py` | PLC session, resets PLC | Cold-reset the application on the PLC, then start it again. |
| `create_coord1_bind_methods.py` | edits project | Create the Coord1CommitBind / Coord1LatchWindowError method POUs under PROGRAM AxisGroupSM so import_all can resolve the new .st files (B3 fix, single bind-commit + single error-snapshot latch for the SYS and FlyEvent CO |
| `create_drain_host_packets.py` | edits project | Create the DrainHostPackets method POU under PROGRAM AxisGroupSM so import_all can resolve the new .st file. |
| `create_ecat_esp_http.py` | edits project, PLC session | Create PRG_EcatEspHttp: a tiny HTTP status page served by the PLC itself for the EasyCAT + ESP32 + encoder slave. |
| `create_ecat_esp_probe.py` | edits project, PLC session | Create PRG_EcatEsp: exercises the EasyCAT PRO + ESP32 slave from EtherCAT_Task. |
| `create_ecat_stepper.py` | edits project, PLC session | Create PRG_EcatStepper: the PLC side of the ESP32 open-loop CSP stepper (firmware/easycat_esp32/src/stepper.h), in EtherCAT_Task (1 ms). |
| `create_event_log.py` | edits project, PLC session | Create PRG_EventLog: 1 ms event timestamp log (GVL.EvHead ring) in EtherCAT_Task, after AxisGroupSM. |
| `create_flush_fly_events.py` | edits project | Create the FlushFlyEvents method POU under PROGRAM AxisGroupSM so import_all can resolve the new .st file (drops the fly events of an ended run or session, telling the host). |
| `create_flyevent_helpers.py` | edits project | Create ArmFiredTrigger + TickTtl method POUs under PROGRAM AxisGroupSM so import_all can resolve the new .st files. |
| `create_fsm_helpers.py` | edits project | Create TripPowerError + TripTimeout method POUs under FUNCTION_BLOCK AxisGroupManager. |
| `create_mb_channels.py` | PLC session | Add Modbus channels to a serial Modbus slave, the way the device editor's "Modbus Slave Channel" tab does. |
| `create_mb_probe.py` | edits project, PLC session | Create PRG_MbProbe: on-demand Modbus RTU diagnostics for the SmartBowlFeeder, called from the Comm task (the Modbus bus cycle task). |
| `create_mppacker_kv_methods.py` | edits project | Move msgpack composition helpers from AxisGroupSM PROGRAM into FB_MpPacker FB. |
| `create_msgpack_v2_helpers.py` | edits project | Create the v2 msgpack helper method POUs under PROGRAM AxisGroupSM. |
| `create_msgpack_v3_helpers.py` | edits project | v3 msgpack helpers: ProbeReplySlot (retry-style, no counter bump), TryAcquireReplySlotOrScratch + CommitReplySlotOrScratch (main dispatcher with scratch fallback). |
| `create_packkv_helpers.py` | edits project | Create PackKv{Str,Dint,Lint,Bool,Real} method POUs under PROGRAM AxisGroupSM. |
| `create_plan_count_cells.py` | edits project | Create the PlanCountCells method POU under PROGRAM AxisGroupSM so import_all can resolve the new .st file (the one retained-plan tape cell count behind the reel move tracker). |
| `create_reset_diag_counters.py` | edits project | Create the ResetDiagCounters method POU under PROGRAM AxisGroupSM so import_all can resolve the new .st file (R8 fix, single canonical RESET_DBG_INFO list). |
| `create_sim_io.py` | edits project, PLC session | Create PRG_SimIo: what the simulated peripherals need to see of the PLC, counted every 1 ms in EtherCAT_Task. |
| `create_update_di_watch.py` | edits project | Create the UpdateDiWatch method POU under PROGRAM AxisGroupSM so import_all can resolve the new .st file (digital-input change events, SYS DI_WATCH). |
| `demo_binding_flow.py` | PLC session | End-to-end wire demo of the tape-binding bench flow (BindingTestPage). |
| `demo_recovery_flow.py` | ? | End-to-end demo of the §4 (2) crash-and-resume flow, driven through the UI's harness (remote_ctrl + remote_harness). |
| `diff_st.py` | read-only | READ-ONLY dry run of import_all.py. |
| `download_and_start.py` | edits project, PLC session | Save project, login to PLC with download (force), start the application. |
| `download_start_virtual.py` | PLC session | FULL DOWNLOAD the current project to the controller and start it. |
| `dump_drive_target_addr.py` | read-only | Print the IEC address (and any mapping) of each ASDA drive's Target Position / Actual Position PDO channel (manual_iec_address holds the address even when it is assigned automatically), for AT-declared read-only taps of  |
| `dump_ethercat.py` | read-only | Dump the EtherCAT configuration of the open project. |
| `dump_modbus.py` | read-only | Every parameter of the Modbus devices (COM port, client, slaves, channels), for timing questions. |
| `dump_serial.py` | read-only | Dump the serial / Modbus branch of the device tree. |
| `dump_task_config.py` | read-only | Print the Task Configuration: every task with its type, priority, interval, watchdog, core affinity (where exposed) and the POUs it calls, plus task-related parameters of the PLC device and the EtherCAT master and SoftMo |
| `dump_task_detail.py` | read-only | Task POU lists and watchdogs, and the axis group objects' settings (planning / bus task, etc.). |
| `export_all.py` | ? | Walk the open project and write every textual object (POU, method, action, property, DUT, GVL, ...) to the codesys_code/ tree on disk. |
| `export_modbus_xml.py` | read-only | READ-ONLY (writes one file outside the project). |
| `find_symbol.py` | read-only | Find where a symbol is declared or bound in the open project: every object's declaration / implementation text, and every device IO mapping (EtherCAT, Modbus, local IO ...). |
| `force_download_start.py` | PLC session | Login with ForceDownload to push the layout-changing GVL update (reMP_info_buf went 256->1024). |
| `import_all.py` | ? | Walk codesys_code/ on disk and push every .st file back into the currently-open project. |
| `import_esi.py` | read-only | Install an EtherCAT ESI file into the local device repository. |
| `modbus_diag_live.py` | read-only, PLC session | Read the Modbus devices' ONLINE diagnostic values from the running controller. |
| `online_change.py` | edits project, PLC session | Save project, run generate_code, then login with Try so CODESYS does an online change. |
| `plc_fetch_cfg.py` | read-only, PLC session | Fetch the runtime's configuration files from the PLC. |
| `plc_files.py` | read-only, PLC session, resets PLC | Connect to the PLC at DEVICE level and list its exposed directories. |
| `probe_bus_cycle.py` | read-only | Print the bus cycle task settings the scripting API exposes, per device. |
| `probe_coord1_bind.py` | ? | Phase 2 verification: drive COORD1_BIND end-to-end via raw TCP. |
| `probe_coord1_drift.py` | PLC session | Phase 1 verification: force ConveyorPulseRaw + bind state, read back OriginNow drift via SYS/GET_COORD1_DEBUG. |
| `probe_coord1_g1_frame.py` | PLC session | Phase 3 verify (host-side): G1 `frame` field dispatch. |
| `probe_coord1_table_interp.py` | PLC session | Phase 2 live interp test: drives the cam table that was loaded by probe_coord1_bind.py Test 4, sweeps ConveyorPulseRaw across the table, reads back Coord1OriginNow each time. |
| `probe_drain_method.py` | ? | Probe DrainHostPackets method content + AxisGroupSM body to debug why SYS handlers stopped answering after the extract. |
| `probe_flyevent_bind.py` | PLC session | Phase 4 step 3 verification: FlyEvent-scheduled COORD1_BIND + window-exit fault. |
| `probe_method_api.py` | edits project | Probe rename mechanisms on a Method POU. |
| `probe_modbus_live.py` | read-only, PLC session | Log in READ-ONLY and ask the running controller whether the Modbus master is alive. |
| `probe_pcs1_track.py` | PLC session | Phase 4 step 2 verification: MC_TrackConveyorBelt links PCS_1. |
| `probe_pulse_trigger.py` | PLC session | Phase 5 verification: M4 PulseTrigger (trig=130). |
| `probe_reel_retain.py` | PLC session, resets PLC | A.1 probe -- does the reel axis position survive PLC restart scenarios? |
| `probe_scratchpad_burst.py` | ? | B.4 -- SCRATCHPAD_WRITE burst-write atomicity / throughput probe. |
| `read_plc_log.py` | read-only | Read the PLC's runtime log (what the IDE shows under Device -> Log, e.g. |
| `remove_dead_helpers.py` | edits project | Tier-A cleanup: remove three POUs that have zero call sites in the entire project. |
| `remove_ringbuf_clear.py` | edits project | Remove FB_RingBufferIndex.Clear method (no live callers; only referenced in a historical comment in CheckAxisGroupReady.st). |
| `remove_ringbuf_init.py` | edits project | Remove FB_RingBufferIndex.init method (inlined into FB_init). |
| `rename_databuffer_methods.py` | edits project | Rename FB_DataBuffer methods to PascalCase (Tier-B cleanup, pair to rename_ringbuf_methods.py). |
| `rename_ringbuf_methods.py` | edits project | Rename FB_RingBufferIndex methods to PascalCase (Tier-B cleanup). |
| `restart_app.py` | PLC session | Stop and start the application on the PLC (no download). |
| `run_msgpack_tests.py` | edits project, PLC session | Drive the PLC-side MsgPack self-test harness (FB_MsgPackTests) in online mode and print results. |
| `save_project.py` | edits project | Persist whatever's currently in the warm session to the project file. |
| `set_a_additional_axis.py` | edits project | NOT IN USE (2026-09-30): the real PLC hangs on the first blended G1 with A as additional axis on SM3 4.20; the machine runs SM3 4.18 + Kin_CAxis. |
| `set_axis_limits.py` | edits project | Axis dynamic limits (the axis' "Dynamic limits": fSWMax*), from the peaks the production flow reached on the machine (2026-09-29, real flow with the vision mock, plus a queued-replay run for the worst segment): delta joi |
| `set_axis_virtual.py` | edits project, PLC session | Set the device-tree "virtual mode" (host parameter bVirtual) of one or more SoftMotion axes, then save the project. |
| `set_bus_cycle.py` | edits project | Set the EtherCAT bus cycle: EtherCAT_Task interval, the master's MasterCycleTime and every slave's DC sync0 / sync1 cycle time. |
| `set_comm_task_period.py` | ? | Set the Comm task (TCP_MSGPAK_Server) period. |
| `set_drive_demand_pdo.py` | edits project | Put the drive's position demand value (0x6062, what the drive's control loop actually uses) into its TxPDO, in place of the touch probe position 0x60BA (same size, unused here), so the PLC sees per cycle what the ASDA di |
| `set_drive_sync_shift.py` | edits project | Set "DC sync0 shift time" on the three ASDA-B3-E slaves (EAxis0/1/2): their SYNC0 fires this much later than the other slaves'. |
| `set_ec_sync_offset.py` | edits project, PLC session | Set the EtherCAT master's SyncOffset (% of the cycle between the frame and SYNC0), then save the project. |
| `set_group_a_axis.py` | edits project | The axis group's A axis (SpiderR tool kinematics Kin_CAxis): SM_Drive_GenericDSP402, drive ID 7, logical device 1 = the second axis of the QEC stepper driver (M2 / "Y", where the rotation motor is wired). |
| `set_mb_comport.py` | edits project | Set Modbus_COM's ComPort, then build. |
| `set_mb_server_address.py` | edits project | Set SmartBowlFeeder's Modbus ServerAddress, then build. |
| `set_modbus_bus_cycle.py` | ? | Pin the Modbus RTU branch to the Comm task. |
| `set_modbus_enabled.py` | edits project | Enable or disable the Modbus RTU branch (Modbus_COM and everything under it: the client port and the SmartBowlFeeder slave). |
| `set_qec_dc.py` | edits project | Turn DC on / off for the QEC stepper slave (station 1006, first on the wire). |
| `set_sm3_420.py` | edits project | NOT IN USE (2026-09-30): the real PLC hangs on the first blended G1 with A as additional axis on SM3 4.20; the machine runs SM3 4.18 + Kin_CAxis. |
| `stamp_build_info.py` | edits project | Stamp BUILD_GIT_SHA / BUILD_TS_MS in GVL.st with the current git rev and a unix-ms timestamp. |
| `stop_then_install.py` | PLC session | Full install: download the project to the PLC and start it. |
| `sweep_elecount.py` | ? | Zero-out ele_count literal initializers + remove redundant adders. |
| `sweep_pack_overflow_guards.py` | ? | Add overflow-latch guards to every FB_MpPacker pack helper. |
| `sweep_packkv.py` | ? | Sweep AxisGroupSM .st files: collapse adjacent PackString(key) + Pack{String,DINT,LINT,Bool,REAL}(val) pairs into single PackKv{Str,Dint,Lint,Bool,Real}(key, val) calls. |
| `virt_hard_clear.py` | PLC session | Hard-clear the virtual-motors force: overwrite to FALSE first (defeats any latched force value), then unforce_all_values, then read-back to confirm. |
| `virtual_motors_force.py` | PLC session | Force GVL.bVirtualMotorsMode_Request := TRUE so EV_HOME_GO_FORCE_SKIP is allowed to fire. |
| `virtual_motors_unforce.py` | PLC session | Clear the virtual-motors force. |
| `viz_belt_arm.py` | ? | Conveyor-pick visualization: sample arm TCP + belt-tracked object position over a window of time, plot as top-down XY view. |
| `viz_full_cycle.py` | PLC session | Full conveyor pick-and-place cycle viz, v2. |
| `viz_pcs1_chase.py` | PLC session | Phase 4 step 2 live viz v3: pure-TCP sampling (50 Hz easy), arm position read via SYS/GET_COORD1_DEBUG so a virtual-axis self-halt doesn't kill the trace mid-motion. |
| `viz_progress_tracking.py` | PLC session | Probe: how does MovementProgress evolve under a moving target? |

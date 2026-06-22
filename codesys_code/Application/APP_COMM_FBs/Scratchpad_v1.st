TYPE Scratchpad_v1 :
STRUCT
    (* Host-owned opaque cursor for crash-and-resume.

       Design contract (decisions_2026-06-22.md §4 (2)):
         - The PLC NEVER interprets any field other than SchemaVersion
           and BootEpoch. Every other field is host-defined opaque
           state. PLC is a durable register, not a brain.
         - schema_version = 0 means "uninitialised / cold-reset wiped".
           PLC writes 1 on first boot. Host treats 0 as invalid and
           goes cold-start.
         - BootEpoch mirrors GVL.BootEpochCount captured at the moment
           of the LAST host write. Host compares on read; mismatch ->
           PLC restarted since the write -> scratchpad is stale ->
           cold-start.

       Memory layout sized to fit comfortably in the SYS reply envelope
       and the retain area budget. Future fields go in `Reserved`. *)
    SchemaVersion     : USINT;
    PlanId            : UDINT;   (* opaque to PLC *)
    PlanIndex         : UDINT;   (* opaque to PLC *)
    IntentKind        : USINT;   (* opaque to PLC -- host-defined enum.
                                    Semantics live in the host (see
                                    `orchestrator/resume.ts` IntentKind);
                                    do NOT branch on this in PLC code,
                                    per memory `plc_stays_generic`. *)
    IntentMovementId  : UDINT;   (* LastAcceptedMovementId at intent write *)
    LastVisionPulse   : UDINT;   (* opaque to PLC *)
    BootEpoch         : UDINT;   (* GVL.BootEpochCount at last write *)
    Reserved          : ARRAY[0..7] OF BYTE;
END_STRUCT
END_TYPE

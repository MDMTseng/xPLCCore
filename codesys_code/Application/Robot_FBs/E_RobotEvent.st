{attribute 'to_string'}
// Robot-event enum. Underlying UDINT.
//
// Explicit numeric values are LOAD-BEARING: host-side memory
// `plc_event_numeric_values.md` documents the wire integers, and any
// shift here would silently break GA_EV from old UIs / test scripts.
//
// Gaps in numbering (1, 3, 5) used to hold EV_BOOT, EV_POWER_OFF,
// EV_GROUP_DISABLE -- all dead (no producer, no CASE branch in
// Transition.st). Removed in the 2026-06-23 cleanup pass to stop new
// contributors from wiring posting sites the FSM cannot route. The
// gaps are reserved against accidental future reuse with conflicting
// semantics.
TYPE E_RobotEvent :
(
    EV_NONE               := 0,   // Default / unposted
    EV_POWER_ON           := 2,   // Host: start the power-on sequence
    EV_GROUP_ENABLE       := 4,   // Host: start the group-enable sequence
    EV_HOME_GO            := 6,   // Host: start the homing sequence
    EV_HOME_GO_FORCE_SKIP := 7,   // Host: skip homing (virtual-motors mode only)
    EV_RESET              := 8,   // Host: universal reset; any state -> UnInited
    EV_ERROR              := 9,   // Internal (supervisor / FB error) or host (manual trip)
    EV_OK                 := 10,  // Internal: handshake within a state's update
    EV_ER                 := 11   // Internal: error sub-flag (currently unused as event)
) UDINT;
END_TYPE

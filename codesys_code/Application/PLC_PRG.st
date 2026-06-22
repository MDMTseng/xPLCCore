PROGRAM PLC_PRG
VAR
    // Placeholder var. Kept (not deleted) because removing PLC_PRG can
    // break the CODESYS task configuration that historically referenced
    // it. The cyclic tasks that actually run the machine are:
    //   - EC_Task     -> AxisGroupSM  (motion + FSM, 1ms / prio 0)
    //   - Comm        -> TCP_MSGPAK_Server  (msgpack server, 5ms / prio 20)
    //   - Planning    -> (empty)
    // See memory `plc_task_config.md`.
    //
    // FB_MsgPackTests's header comment claims PLC_PRG invokes it; that
    // call site never existed in this branch -- flip
    // GVL.bRunMsgPackTests in an online session to run the tests, the
    // FB instance is held inside FB_MsgPackTests's own scope when
    // invoked manually from a watch list.
    placeholder : INT := 0;
END_VAR
(* =========== IMPLEMENTATION =========== *)

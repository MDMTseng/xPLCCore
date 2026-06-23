METHOD ProbeReplySlot : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Like TryAcquireReplySlot but does NOT bump any drop counters on
// full-ring failure. Use for RETRY-style emits (MOVE_DONE, ST_CHG)
// where the same packet is retried every scan until either the ring
// drains or PENDING_RETRY_MAX_SCANS gives up -- counting every retry
// failure would over-report the actual drop count.
//
// Returns TRUE if a slot was reserved (ResponsePacketPointer set,
// MsgPakInfoClear ran). Returns FALSE without side effects on full.
// Caller is responsible for its own retry-limit + class-specific
// drop counter on the final give-up.
IF (GVL.reMP_info_ridx.Space() > 0) THEN
    ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
    MsgPakInfoClear(ResponsePacketPointer^);
    MessagePackerFb.BindSlot(ResponsePacketPointer);  // builder pattern: pack helpers target this slot
    ProbeReplySlot := TRUE;
ELSE
    ProbeReplySlot := FALSE;
END_IF;

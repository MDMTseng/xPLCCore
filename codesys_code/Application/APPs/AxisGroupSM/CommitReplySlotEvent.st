METHOD CommitReplySlotEvent : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Finalise an UNSOLICITED EVENT slot reserved by TryAcquireReplySlot:
// run Wrapup (which auto-appends `seq` -- UI tracks gaps via seq for
// events that have no `id` to correlate against) and PushHead.
// Request replies use CommitReplySlotAck which adds id + ack first.
// Not pushed when Wrapup failed (see CommitReplySlotAck).
IF MsgPakInfoWrapup(ResponsePacketPointer^) THEN
    GVL.reMP_info_ridx.PushHead();
END_IF;
CommitReplySlotEvent := TRUE;

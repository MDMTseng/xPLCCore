METHOD CommitReplySlotEvent : BOOL
VAR_INPUT
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Finalise an UNSOLICITED EVENT slot reserved by TryAcquireReplySlot:
// run Wrapup (which auto-appends `seq` -- UI tracks gaps via seq for
// events that have no `id` to correlate against) and PushHead.
// Request replies use CommitReplySlotAck which adds id + ack first.
MsgPakInfoWrapup(ResponsePacketPointer^);
GVL.reMP_info_ridx.PushHead();
CommitReplySlotEvent := TRUE;

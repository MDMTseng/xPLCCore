METHOD CommitReplySlotAck : BOOL
VAR_INPUT
    diCmdId : DINT;
    bAck    : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Finalise a reply slot reserved by TryAcquireReplySlot: append the
// universal `id` + `ack` tail fields, run Wrapup (which auto-appends
// `seq`), and PushHead so the Comm task sees it.
// Use for REQUEST REPLIES that carry a correlation id and ack/nack.
// Events without `id` / `ack` use CommitReplySlotEvent instead.
PackKvDint(ADR('id'), diCmdId);
PackKvBool(ADR('ack'), bAck);
MsgPakInfoWrapup(ResponsePacketPointer^);
GVL.reMP_info_ridx.PushHead();
CommitReplySlotAck := TRUE;

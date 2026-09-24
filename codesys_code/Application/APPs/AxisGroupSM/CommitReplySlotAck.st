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
MessagePackerFb.PackKvDint(ADR('id'), diCmdId);
MessagePackerFb.PackKvBool(ADR('ack'), bAck);
// A reply whose Wrapup failed (a Pack* overran the slot) has no valid
// header: pushing it sent the host garbage. Drop it instead; Wrapup
// counted it in ReMpOverflowDropCount and the host sees a reply timeout.
IF MsgPakInfoWrapup(ResponsePacketPointer^) THEN
    GVL.reMP_info_ridx.PushHead();
END_IF;
CommitReplySlotAck := TRUE;

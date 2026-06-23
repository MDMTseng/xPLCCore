METHOD CommitReplySlotOrScratch : BOOL
VAR_INPUT
    diCmdId           : DINT;
    bAck              : BOOL;
    pDropClassCounter : POINTER TO UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Finalise a slot reserved by TryAcquireReplySlotOrScratch.
// Appends id+ack KV tail and runs Wrapup either way. On the scratch
// path (SlotAvailable=FALSE) does NOT PushHead and bumps the generic
// + class-specific drop counters.
PackKvDint(ADR('id'), diCmdId);
PackKvBool(ADR('ack'), bAck);
MsgPakInfoWrapup(ResponsePacketPointer^);
IF SlotAvailable THEN
    GVL.reMP_info_ridx.PushHead();
ELSE
    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
    IF pDropClassCounter <> 0 THEN
        pDropClassCounter^ := pDropClassCounter^ + 1;
    END_IF;
END_IF;
CommitReplySlotOrScratch := TRUE;

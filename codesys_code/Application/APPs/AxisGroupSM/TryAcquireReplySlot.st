METHOD TryAcquireReplySlot : BOOL
VAR_INPUT
    pDropClassCounter : POINTER TO UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Reply / event slot acquisition. Returns TRUE if a free reMP_info
// slot was reserved (caller may proceed to pack + CommitReplySlot*),
// FALSE if the ring is full (caller skips the pack body; drop counter
// already bumped here).
//
// On success: ResponsePacketPointer points at the reserved slot,
// MsgPakInfoClear has been called (ele_count = 0, whead = buf base),
// caller goes straight to PackKv* / PackKvMap etc.
//
// On full-ring failure: GVL.ReMpDropCount + the class-specific counter
// (pDropClassCounter, e.g. ADR(GVL.ReMpDropCount_TriggerEvent)) are
// both incremented. Pass 0 if no class-specific counter applies.
//
// Producer-only -- caller MUST NOT call .ConsumeTail on the reply ring.
// Pairs with CommitReplySlotAck (request reply) or CommitReplySlotEvent
// (unsolicited push event).
IF (GVL.reMP_info_ridx.Space() > 0) THEN
    ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
    MsgPakInfoClear(ResponsePacketPointer^);
    TryAcquireReplySlot := TRUE;
ELSE
    GVL.ReMpDropCount := GVL.ReMpDropCount + 1;
    IF pDropClassCounter <> 0 THEN
        pDropClassCounter^ := pDropClassCounter^ + 1;
    END_IF;
    TryAcquireReplySlot := FALSE;
END_IF;

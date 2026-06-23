METHOD TryAcquireReplySlotOrScratch : BOOL
VAR_INPUT
    pDropClassCounter : POINTER TO UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Main-dispatcher slot acquisition with scratch fallback. Always
// succeeds at giving the caller a slot to pack into -- if the real
// ring is full it routes the pack into GVL.reMP_info_scratch so the
// long ELSIF dispatch chain in DrainHostPackets can stay unchanged.
//
// Sets SlotAvailable as the return value:
//   TRUE  -> real slot reserved, CommitReplySlotOrScratch will PushHead
//   FALSE -> packed into scratch; CommitReplySlotOrScratch will NOT
//            PushHead (would corrupt a slot a consumer is reading)
//            and will bump the class-specific drop counter.
//
// pDropClassCounter is bumped IN CommitReplySlotOrScratch on the FALSE
// branch (not here) so callers that bail out before commit don't get
// counted.
IF (GVL.reMP_info_ridx.Space() > 0) THEN
    SlotAvailable := TRUE;
    ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.GetHead()]);
ELSE
    SlotAvailable := FALSE;
    ResponsePacketPointer := ADR(GVL.reMP_info_scratch);
END_IF;
MsgPakInfoClear(ResponsePacketPointer^);
TryAcquireReplySlotOrScratch := SlotAvailable;

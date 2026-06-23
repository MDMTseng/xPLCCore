METHOD BindSlot : BOOL
VAR_INPUT
    pBindSlot : POINTER TO MsgPakInfo;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Stash the active reply/event slot. All subsequent PackKv* / PackPair*
// / PackElem* / PackKvMap / PackKvArray calls target this slot until
// the next BindSlot. Called automatically by the AxisGroupSM slot-
// lifecycle helpers (TryAcquireReplySlot / ProbeReplySlot /
// TryAcquireReplySlotOrScratch); retry-style sites that hand-roll
// slot acquisition must call this themselves.
pSlot := pBindSlot;
// Clear the overflow latch for the new slot. Each acquire starts fresh
// -- old overflow state from a previous slot must not carry forward.
bSlotOverflowed := FALSE;
BindSlot := TRUE;

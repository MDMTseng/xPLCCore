METHOD PackKvStr : BOOL
VAR_INPUT
    psKey : POINTER TO STRING;
    psVal : POINTER TO STRING;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Pack one msgpack key:string-value pair into the BOUND slot, bump
// outer ele_count by 1. Slot is set by BindSlot (auto-bound by the
// AxisGroupSM acquire helpers). Pre-2026-06-23 this lived on the
// AxisGroupSM PROGRAM and used an implicit ResponsePacketPointer;
// builder pattern puts msgpack composition where it logically lives
// (the packer) and AxisGroupSM keeps only the slot-lifecycle / FSM
// concerns.
IF bSlotOverflowed THEN PackKvStr := FALSE; RETURN; END_IF
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psKey);
pSlot^.whead := PackString(pStart := pSlot^.whead, psValue := psVal);
pSlot^.ele_count := pSlot^.ele_count + 1;
IF (pSlot^.whead - pSlot^.buf) > TO_DINT(pSlot^.buf_capacity) THEN
    bSlotOverflowed := TRUE;
    PackKvStr := FALSE;
ELSE
    PackKvStr := TRUE;
END_IF

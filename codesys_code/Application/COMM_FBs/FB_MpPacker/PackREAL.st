METHOD PackREAL : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	rValue : REAL;
END_VAR
VAR
	rSwappedValue : REAL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Bound-slot guard (2026-10-04): if pStart lies in the slot bound by
// BindSlot and the up to 5 bytes would not fit, write nothing and flag the
// slot (the post-write checks of the PackKv* helpers only saw the
// overrun after the bytes were already in the next slot).
IF (pSlot <> 0) AND (pStart >= pSlot^.buf) AND (pStart <= pSlot^.buf + pSlot^.buf_capacity) THEN
	IF (pStart - pSlot^.buf) + 5 > TO_DINT(pSlot^.buf_capacity) THEN
		bSlotOverflowed := TRUE;
		pSlot^.overflowed := TRUE;
		PackREAL := pStart;
		RETURN;
	END_IF;
END_IF;

	
    pStart^ := 16#CA;//header for real 32bit
	Swap32To(ADR(rValue),pStart+1);
    PackREAL := pStart + 1+4;
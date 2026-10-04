METHOD PackBool : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	bValue : BOOL;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Bound-slot guard (2026-10-04): if pStart lies in the slot bound by
// BindSlot and the up to 1 bytes would not fit, write nothing and flag the
// slot (the post-write checks of the PackKv* helpers only saw the
// overrun after the bytes were already in the next slot).
IF pSlot <> 0 THEN	// nested on purpose: ST's AND evaluates every operand (no short circuit)
	IF (pStart >= pSlot^.buf) AND (pStart <= pSlot^.buf + pSlot^.buf_capacity) THEN
		IF (pStart - pSlot^.buf) + 1 > TO_DINT(pSlot^.buf_capacity) THEN
			bSlotOverflowed := TRUE;
			pSlot^.overflowed := TRUE;
			PackBool := pStart;
			RETURN;
		END_IF;
	END_IF;
END_IF;
	IF bValue THEN
		pStart^ := 16#C3; // true format
	ELSE
		pStart^ := 16#C2; // false format
	END_IF
	PackBool := pStart + 1;
METHOD PackMapHeader : POINTER TO BYTE
VAR_INPUT
	pStart : POINTER TO BYTE;
	uiCount : UINT;
	bForceLargeField:BOOL:=FALSE;
END_VAR
VAR
	pWordCount : POINTER TO WORD;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Bound-slot guard (2026-10-04): if pStart lies in the slot bound by
// BindSlot and the up to 3 bytes would not fit, write nothing and flag the
// slot (the post-write checks of the PackKv* helpers only saw the
// overrun after the bytes were already in the next slot).
IF pSlot <> 0 THEN	// nested on purpose: ST's AND evaluates every operand (no short circuit)
	IF (pStart >= pSlot^.buf) AND (pStart <= pSlot^.buf + pSlot^.buf_capacity) THEN
		IF (pStart - pSlot^.buf) + 3 > TO_DINT(pSlot^.buf_capacity) THEN
			bSlotOverflowed := TRUE;
			pSlot^.overflowed := TRUE;
			PackMapHeader := pStart;
			RETURN;
		END_IF;
	END_IF;
END_IF;
	IF uiCount <= 15 AND bForceLargeField=FALSE THEN
		pStart^ := 16#80 + UINT_TO_BYTE(uiCount);
		PackMapHeader := pStart + 1;
	ELSIF uiCount <= 65535 OR bForceLargeField=TRUE THEN
		pStart^ := 16#DE;
		pStart := pStart + 1;
		pWordCount := pStart;
		Swap16To(ADR(uiCount),pWordCount);
		PackMapHeader := pStart + 2;
	END_IF;
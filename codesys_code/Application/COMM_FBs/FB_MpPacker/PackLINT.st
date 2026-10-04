METHOD PackLINT : POINTER TO BYTE
VAR_INPUT
    pStart  : POINTER TO BYTE;
    liValue : LINT;
END_VAR
VAR
    tmpInt   : INT;
    tmpDint  : DINT;
    pNext    : POINTER TO BYTE;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Bound-slot guard (2026-10-04): if pStart lies in the slot bound by
// BindSlot and the up to 9 bytes would not fit, write nothing and flag the
// slot (the post-write checks of the PackKv* helpers only saw the
// overrun after the bytes were already in the next slot).
IF pSlot <> 0 THEN	// nested on purpose: ST's AND evaluates every operand (no short circuit)
	IF (pStart >= pSlot^.buf) AND (pStart <= pSlot^.buf + pSlot^.buf_capacity) THEN
		IF (pStart - pSlot^.buf) + 9 > TO_DINT(pSlot^.buf_capacity) THEN
			bSlotOverflowed := TRUE;
			pSlot^.overflowed := TRUE;
			PackLINT := pStart;
			RETURN;
		END_IF;
	END_IF;
END_IF;
// Compact signed encoding (mirrors PackDINT). Wire is interoperable with
// strict MessagePack decoders that reject overlong forms; PLC reader
// accepts every marker via TryReadINT64 / UnpackNext, so shrinking is a
// pure bandwidth win.

IF liValue >= 0 AND liValue <= 127 THEN
    // positive fixint (0x00-0x7F): 1 byte
    pStart^ := DINT_TO_BYTE(LINT_TO_DINT(liValue));
    PackLINT := pStart + 1;

ELSIF liValue >= -32 AND liValue < 0 THEN
    // negative fixint (0xE0-0xFF): 1 byte
    pStart^ := DINT_TO_BYTE(LINT_TO_DINT(liValue));
    PackLINT := pStart + 1;

ELSIF liValue >= -128 AND liValue <= 127 THEN
    // int 8 (0xD0): 2 bytes
    pStart^ := 16#D0;
    pNext := pStart + 1;
    pNext^ := DINT_TO_BYTE(LINT_TO_DINT(liValue));
    PackLINT := pStart + 2;

ELSIF liValue >= -32768 AND liValue <= 32767 THEN
    // int 16 (0xD1): 3 bytes, big-endian
    pStart^ := 16#D1;
    tmpInt := LINT_TO_INT(liValue);
    Swap16To(ADR(tmpInt), pStart + 1);
    PackLINT := pStart + 3;

ELSIF liValue >= -2147483648 AND liValue <= 2147483647 THEN
    // int 32 (0xD2): 5 bytes, big-endian
    pStart^ := 16#D2;
    tmpDint := LINT_TO_DINT(liValue);
    Swap32To(ADR(tmpDint), pStart + 1);
    PackLINT := pStart + 5;

ELSE
    // int 64 (0xD3): 9 bytes, big-endian
    pStart^ := 16#D3;
    Swap64To(ADR(liValue), pStart + 1);
    PackLINT := pStart + 9;

END_IF;

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

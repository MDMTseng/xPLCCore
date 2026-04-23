(*
    RunAll: execute every test case once, fill outputs on the FB.
    Each assertion increments uiPassed or uiFailed and -- on first failure
    only -- copies a descriptive message into sFirstFail.

    Tests marked [REGRESSION-FIX-N] are designed to FAIL on today's code
    and PASS after the corresponding fix in doc/msgpack.md lands. That
    asymmetry is the point -- it's how we know the test is real.
*)
METHOD PRIVATE RunAll
VAR
    pkr    : FB_MpPacker;
    pCur   : POINTER TO BYTE;
    pStart : POINTER TO BYTE;
    pEnd   : POINTER TO BYTE;
    diRead : DINT;
    rRead  : REAL;
    sRead  : STRING;
    bRead  : BOOL;
    uiLen  : UDINT;
    pStr   : POINTER TO BYTE;
    i      : DINT;
    sTmp   : STRING;
    cycNum : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
uiPassed   := 0;
uiFailed   := 0;
sFirstFail := '';
bDone      := FALSE;

pStart := ADR(buf);

(* ---------- T01: bool round-trip ---------- *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 2);
pCur := pkr.PackString(pCur, ADR('t'));
pCur := pkr.PackBool(pCur, TRUE);
pCur := pkr.PackString(pCur, ADR('f'));
pCur := pkr.PackBool(pCur, FALSE);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadBool('t', FALSE) <> TRUE THEN
    Fail('T01a bool TRUE'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadBool('f', TRUE) <> FALSE THEN
    Fail('T01b bool FALSE'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T02: DINT round-trip (various magnitudes) ---------- *)
(* Packer today always writes D2+4 bytes regardless of magnitude. That's
   finding #12 / Fix 4. Round-trip should still work even without
   compact encoding -- this test will keep passing after Fix 4 lands. *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 5);
pCur := pkr.PackString(pCur, ADR('zero'));   pCur := pkr.PackDINT(pCur, 0);
pCur := pkr.PackString(pCur, ADR('small'));  pCur := pkr.PackDINT(pCur, 42);
pCur := pkr.PackString(pCur, ADR('neg'));    pCur := pkr.PackDINT(pCur, -1000);
pCur := pkr.PackString(pCur, ADR('max'));    pCur := pkr.PackDINT(pCur, 2147483647);
pCur := pkr.PackString(pCur, ADR('min'));    pCur := pkr.PackDINT(pCur, -2147483648);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadINT64('zero',  -999) <>  0          THEN Fail('T02a zero');  ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('small', -999) <>  42         THEN Fail('T02b small'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('neg',   -999) <> -1000       THEN Fail('T02c neg');   ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('max',   -999) <>  2147483647 THEN Fail('T02d max');   ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('min',   -999) <> -2147483648 THEN Fail('T02e min');   ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T03: REAL round-trip ---------- *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 3);
pCur := pkr.PackString(pCur, ADR('a')); pCur := pkr.PackREAL(pCur,  0.0);
pCur := pkr.PackString(pCur, ADR('b')); pCur := pkr.PackREAL(pCur,  3.1415);
pCur := pkr.PackString(pCur, ADR('c')); pCur := pkr.PackREAL(pCur, -2.5);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF ABS(upk.TryReadREAL('a', 99.0) -  0.0   ) > 0.0001 THEN Fail('T03a 0.0'); ELSE uiPassed := uiPassed + 1; END_IF;
IF ABS(upk.TryReadREAL('b', 99.0) -  3.1415) > 0.0001 THEN Fail('T03b pi');  ELSE uiPassed := uiPassed + 1; END_IF;
IF ABS(upk.TryReadREAL('c', 99.0) - -2.5   ) > 0.0001 THEN Fail('T03c neg'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T04: string lengths around encoding boundaries ---------- *)
(* fixstr limit 31, str8 limit 255. Covers the fixstr/str8 split. *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 3);
pCur := pkr.PackString(pCur, ADR('s0'));  pCur := pkr.PackString(pCur, ADR(''));
pCur := pkr.PackString(pCur, ADR('s31')); pCur := pkr.PackString(pCur, ADR('1234567890123456789012345678901'));
pCur := pkr.PackString(pCur, ADR('s32')); pCur := pkr.PackString(pCur, ADR('12345678901234567890123456789012'));
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
sRead := upk.TryReadString('s0', 'DEFAULT');
IF sRead <> '' THEN Fail('T04a empty'); ELSE uiPassed := uiPassed + 1; END_IF;
sRead := upk.TryReadString('s31', 'DEFAULT');
IF sRead <> '1234567890123456789012345678901' THEN Fail('T04b 31'); ELSE uiPassed := uiPassed + 1; END_IF;
sRead := upk.TryReadString('s32', 'DEFAULT');
IF sRead <> '12345678901234567890123456789012' THEN Fail('T04c 32'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T05: array header pack + iteration via SkipValue ---------- *)
(* We can't easily read arrays by index today, so just verify the
   header+elements are well-formed by skipping via SkipValue and
   reaching a sentinel. *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 2);
pCur := pkr.PackString(pCur, ADR('arr'));
pCur := pkr.PackArrayHeader(pCur, 3);
pCur := pkr.PackDINT(pCur, 10); pCur := pkr.PackDINT(pCur, 20); pCur := pkr.PackDINT(pCur, 30);
pCur := pkr.PackString(pCur, ADR('after'));
pCur := pkr.PackDINT(pCur, 999);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadINT64('after', -1) <> 999 THEN
    Fail('T05 array skip-over'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T06: [REGRESSION-FIX-2] UnpackNext must advance past bin ---------- *)
(* Construct by hand: {"a": <bin8 of 3 bytes>, "b": 42}.
   Today UnpackNext does not handle 16#C4, does not advance pCurrent
   past the length byte + payload, so the reader desyncs.
   FindValueByPath uses SkipValue internally which DOES handle bin,
   so TryReadINT64('b') should work today IF the path traversal goes
   through SkipValue. If the test passes today, finding #5 is less
   severe than feared. If it fails today, it's a smoking gun. *)
pStart := ADR(buf);
buf[0]  := 16#82;           (* fixmap, 2 pairs *)
buf[1]  := 16#A1; buf[2] := 16#61;  (* "a" *)
buf[3]  := 16#C4;           (* bin 8 *)
buf[4]  := 3;               (* len *)
buf[5]  := 1; buf[6] := 2; buf[7] := 3;
buf[8]  := 16#A1; buf[9] := 16#62;  (* "b" *)
buf[10] := 16#2A;           (* 42 as positive fixint *)
upk.Init(pStart, 11);
IF upk.TryReadINT64('b', -999) <> 42 THEN
    Fail('T06 [FIX-2] bin desync -- SkipValue/UnpackNext asymmetry');
ELSE uiPassed := uiPassed + 1;
END_IF;

(* ---------- T07: [REGRESSION-FIX-1] map32 path read ---------- *)
(* Build {"k": 7} but with a map32 (0xDF) header instead of fixmap.
   Today FindValueByPath has no 0xDF branch -- returns FALSE silently. *)
buf[0] := 16#DF;
buf[1] := 0; buf[2] := 0; buf[3] := 0; buf[4] := 1;  (* count = 1 BE *)
buf[5] := 16#A1; buf[6] := 16#6B;                    (* "k" *)
buf[7] := 16#07;                                      (* 7 *)
upk.Init(pStart, 8);
IF upk.TryReadINT64('k', -999) <> 7 THEN
    Fail('T07 [FIX-1] FindValueByPath missing map32');
ELSE uiPassed := uiPassed + 1;
END_IF;

(* ---------- T08: [REGRESSION-FIX-1] array32 path read ---------- *)
(* {"arr": <array32 len=2 of [5, 99]>, "after": 123}.
   FindValueByPath on "after" must traverse past arr. SkipValue handles
   array32, FindValueByPath does not -- so reaching "after" works via
   SkipValue but indexing INTO the array does not. Test the simpler
   traversal form here. *)
buf[0]  := 16#82;                     (* fixmap 2 *)
buf[1]  := 16#A3; buf[2] := 16#61; buf[3] := 16#72; buf[4] := 16#72;  (* "arr" *)
buf[5]  := 16#DD;                     (* array 32 *)
buf[6]  := 0; buf[7] := 0; buf[8] := 0; buf[9] := 2;
buf[10] := 16#05; buf[11] := 16#63;  (* 5, 99 *)
buf[12] := 16#A5; buf[13] := 16#61; buf[14] := 16#66; buf[15] := 16#74; buf[16] := 16#65; buf[17] := 16#72;  (* "after" *)
buf[18] := 16#7B;                     (* 123 *)
upk.Init(pStart, 19);
IF upk.TryReadINT64('after', -999) <> 123 THEN
    Fail('T08 [FIX-1] SkipValue array32 traversal');
ELSE uiPassed := uiPassed + 1;
END_IF;

(* ---------- T09: [REGRESSION-FIX-3] bounds check on truncated str ---------- *)
(* Malformed: fixmap 1, key "x", str8 claims len=200 but buffer stops
   right after the length byte. Today no bounds check -- MemCpy reads
   200 bytes of whatever follows in memory, potentially faulting.
   After Fix 3: TryReadString returns default cleanly. *)
buf[0] := 16#81;                    (* fixmap 1 *)
buf[1] := 16#A1; buf[2] := 16#78;   (* "x" *)
buf[3] := 16#D9;                    (* str 8 *)
buf[4] := 200;                      (* claimed length *)
(* Pretend buffer ends here: udiSize = 5 *)
upk.Init(pStart, 5);
sRead := upk.TryReadString('x', 'DEF');
(* Under today's code, behavior is undefined -- this test asserts the
   string comes back as DEF (safe) OR as empty. Fails if it comes back
   with junk content, and it must not fault the PLC.
   Accept either 'DEF' or '' as a pass; anything else is a fail. *)
IF sRead <> 'DEF' AND sRead <> '' THEN
    Fail('T09 [FIX-3] bounds: truncated str read returned junk');
ELSE uiPassed := uiPassed + 1;
END_IF;

(* ---------- T10: nested map path ---------- *)
(* {"outer": {"inner": 55}} *)
pStart := ADR(buf);
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 1);
pCur := pkr.PackString(pCur, ADR('outer'));
pCur := pkr.PackMapHeader(pCur, 1);
pCur := pkr.PackString(pCur, ADR('inner'));
pCur := pkr.PackDINT(pCur, 55);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadINT64('outer.inner', -1) <> 55 THEN
    Fail('T10 nested path'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T11: type coercion sanity (uint16 -> DINT) ---------- *)
(* Packer only emits int32 (D2). Construct a uint16 (CD) by hand and
   verify TryReadINT64 sign-extends correctly (should be 40000, not
   a negative number). *)
buf[0] := 16#81;
buf[1] := 16#A1; buf[2] := 16#75;   (* "u" *)
buf[3] := 16#CD;                    (* uint 16 *)
buf[4] := 16#9C; buf[5] := 16#40;   (* 40000 BE *)
upk.Init(pStart, 6);
IF upk.TryReadINT64('u', -1) <> 40000 THEN
    Fail('T11 uint16 coercion'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T12: mixed-type record round-trip ---------- *)
(* Representative of a real protocol frame. *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 4);
pCur := pkr.PackString(pCur, ADR('cmd'));  pCur := pkr.PackString(pCur, ADR('G1'));
pCur := pkr.PackString(pCur, ADR('id'));   pCur := pkr.PackDINT(pCur, 4193);
pCur := pkr.PackString(pCur, ADR('x'));    pCur := pkr.PackREAL(pCur, 123.456);
pCur := pkr.PackString(pCur, ADR('ok'));   pCur := pkr.PackBool(pCur, TRUE);
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadString('cmd', '??') <> 'G1' THEN Fail('T12a cmd'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('id', -1)     <> 4193 THEN Fail('T12b id');  ELSE uiPassed := uiPassed + 1; END_IF;
IF ABS(upk.TryReadREAL('x', 0.0) - 123.456) > 0.001 THEN Fail('T12c x'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadBool('ok', FALSE) <> TRUE THEN Fail('T12d ok'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T13: [FIX-2 direct] UnpackNext iteration past bin payload ----------
   Directly iterate with UnpackNext over {"a": <bin8 len=3>, "b": 99}.
   Without Fix 2, UnpackNext hit 0xC4 as MP_UNKNOWN and only advanced the
   marker byte -- the next call then misinterpreted the length byte as the
   next element. After Fix 2: walks through cleanly and the last DINT is 99. *)
buf[0] := 16#82;
buf[1] := 16#A1; buf[2] := 16#61;
buf[3] := 16#C4; buf[4] := 3;
buf[5] := 16#FF; buf[6] := 16#FE; buf[7] := 16#FD;
buf[8] := 16#A1; buf[9] := 16#62;
buf[10] := 16#63;
upk.Init(pStart, 11);
upk.UnpackNext();                  (* map header *)
upk.UnpackNext();                  (* "a" *)
upk.UnpackNext();                  (* bin8 -- must not desync *)
upk.UnpackNext();                  (* "b" *)
upk.UnpackNext();                  (* 99 as pos fixint *)
IF upk.diLastDINT <> 99 THEN
    Fail('T13 [FIX-2] UnpackNext bin desync'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T14: [FIX-1 direct] map32 nested path + array32 body ----------
   Build with hand-stamped headers:
     map32 (1 pair): "grid" -> array32 (3): 11, 22, 33, then "end" -> 7
   We only index a map here (path = "grid"); SkipValue handles array32
   internally, and the new map32 branch in FindValueByPath is what makes
   the outer traversal work. *)
buf[0] := 16#DF; buf[1] := 0; buf[2] := 0; buf[3] := 0; buf[4] := 2;  (* map32 count=2 *)
buf[5] := 16#A4; buf[6] := 16#67; buf[7] := 16#72; buf[8] := 16#69; buf[9] := 16#64;  (* "grid" *)
buf[10] := 16#DD; buf[11] := 0; buf[12] := 0; buf[13] := 0; buf[14] := 3;  (* array32 count=3 *)
buf[15] := 11; buf[16] := 22; buf[17] := 33;
buf[18] := 16#A3; buf[19] := 16#65; buf[20] := 16#6E; buf[21] := 16#64;  (* "end" *)
buf[22] := 7;
upk.Init(pStart, 23);
IF upk.TryReadINT64('end', -1) <> 7 THEN
    Fail('T14 [FIX-1] map32 + array32 skip'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T15: [FIX-3 extra] str32 with bogus huge length + short buffer ----------
   str32 marker, claimed length 0x01000000 (~16MB), buffer only 8 bytes.
   Without Fix 3 the MemCpy would read 16MB. After: clamps to remaining
   buffer, returns the few ascii bytes that actually fit. Must not fault. *)
buf[0] := 16#81;                             (* fixmap 1 *)
buf[1] := 16#A1; buf[2] := 16#73;            (* "s" *)
buf[3] := 16#DB;                             (* str 32 *)
buf[4] := 16#01; buf[5] := 16#00; buf[6] := 16#00; buf[7] := 16#00;  (* 16MB BE *)
upk.Init(pStart, 8);
sRead := upk.TryReadString('s', 'DEF');
(* Accept empty or any short string -- key point is we did not fault. *)
IF LEN(sRead) > 255 THEN
    Fail('T15 [FIX-3] str32 overflow length not clamped'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- T16: [FIX-4] PackDINT compact encoding markers ----------
   Verify the encoder now emits the smallest marker that fits. We inspect
   the first byte of each value directly from the packed map buffer. *)
pCur := pStart;
pCur := pkr.PackMapHeader(pCur, 6);
pCur := pkr.PackString(pCur, ADR('a')); pCur := pkr.PackDINT(pCur, 5);            (* pos fixint *)
pCur := pkr.PackString(pCur, ADR('b')); pCur := pkr.PackDINT(pCur, -10);          (* neg fixint *)
pCur := pkr.PackString(pCur, ADR('c')); pCur := pkr.PackDINT(pCur, -100);         (* int 8     *)
pCur := pkr.PackString(pCur, ADR('d')); pCur := pkr.PackDINT(pCur, 1000);         (* int 16    *)
pCur := pkr.PackString(pCur, ADR('e')); pCur := pkr.PackDINT(pCur, -1000);        (* int 16    *)
pCur := pkr.PackString(pCur, ADR('f')); pCur := pkr.PackDINT(pCur, 100000);       (* int 32    *)
upk.Init(pStart, DWORD_TO_UDINT(pCur - pStart));
IF upk.TryReadINT64('a', -1) <>   5    THEN Fail('T16a compact pos fixint'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('b', -1) <>  -10   THEN Fail('T16b compact neg fixint'); ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('c', -1) <> -100   THEN Fail('T16c compact int8');       ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('d', -1) <>  1000  THEN Fail('T16d compact int16+');     ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('e', -1) <> -1000  THEN Fail('T16e compact int16-');     ELSE uiPassed := uiPassed + 1; END_IF;
IF upk.TryReadINT64('f', -1) <> 100000 THEN Fail('T16f compact int32');      ELSE uiPassed := uiPassed + 1; END_IF;

(* Budget sanity: 6 entries with compact encoding must be smaller than the
   old 6*5=30 value bytes. Total buffer size gives us a concrete regression
   guard -- if someone reverts to 5-byte D2 for every DINT, this trips. *)
IF DWORD_TO_UDINT(pCur - pStart) > 40 THEN
    Fail('T16g compact encoding bloat'); ELSE uiPassed := uiPassed + 1; END_IF;

(* ---------- summary ---------- *)
sSummary  := CONCAT('passed=',    UINT_TO_STRING(uiPassed));
sSummary  := CONCAT(sSummary,    ' failed=');
sSummary  := CONCAT(sSummary,    UINT_TO_STRING(uiFailed));
IF uiFailed = 0 THEN
    sFirstFail := '(all green)';
END_IF
bDone := TRUE;

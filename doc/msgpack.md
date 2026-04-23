# MessagePack library review (PLC side)

Survey of [`codesys_code/Application/COMM_FBs/FB_MpPacker/`](../codesys_code/Application/COMM_FBs/FB_MpPacker/)
and [`codesys_code/Application/COMM_FBs/FB_MpUnpacker/`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/)
(10 + 14 files, ~1244 lines). Works today. This file is the working
list for tightening it up without changing the wire format.

Wire format is stable and co-owned with
[`@msgpack/msgpack`](../package.json) on the host — **do not change
what we emit or accept**, only how the ST code is organized and
bounded.

---

## Structural findings

### Read side

1. **Four parallel marker-dispatch tables.**
   [`UnpackNext.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/UnpackNext.st),
   [`SkipValue.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/SkipValue.st),
   [`TryReadREAL.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/TryReadREAL.st),
   [`TryReadINT64.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/TryReadINT64.st),
   [`TryReadUINT64.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/TryReadUINT64.st),
   [`TryReadINTBuffer.st`](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/TryReadINTBuffer.st)
   each implement the same `CASE byMarker OF 16#CC, 16#CD, … 16#D3, 16#CA, 16#CB`
   ladder. 5 places to edit whenever the spec is extended. Drift is
   already visible: UnpackNext clamps `uint 64` → DINT silently;
   TryReadREAL converts fully; TryReadINT64 clamps; TryReadUINT64
   clamps negatives → default.

2. **`FindValueByPath` is missing `map 32` / `array 32` branches.**
   Only `fixmap`/`map 16` and `fixarray`/`array 16` are handled. Any
   container ≥ 65536 entries → silent lookup failure, not a NAK.
   `SkipValue` handles both sizes, so a deeply nested field below a
   large map will be reached by skipping but not by pathing — the
   two functions disagree on what's addressable.

3. **`FindValueByPath` allocates a temp `FB_MpUnpacker` per map
   lookup.**
   [line 14](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/FindValueByPath.st#L14)
   — `fbTempUnpacker : FB_MpUnpacker;` inside VAR. For a map with N
   keys the walk does N × (Init + UnpackNext + STRING copy into
   `sLastString`). A pointer-level key-match primitive
   `PeekStringEquals(pPos, sKey)` would be O(N) bytes with no copy.

4. **Every `TryRead*` re-walks the path from `pStart`.**
   Reading a record with M fields is O(M²) traversal. A cursor-style
   API on a map (`IterateFields → {pKey, pValue}`) would be O(M).
   Not urgent at current message sizes, but the cost scales with
   message depth.

5. **`UnpackNext` doesn't implement bin / ext markers.**
   `16#C4`/`C5`/`C6` and `16#C7`–`D8` silently fall through to
   `MP_UNKNOWN` without advancing `pCurrent` past the payload. If a
   binary field ever enters the stream from the host, the parser
   desyncs on it. `SkipValue` handles them; `UnpackNext` doesn't.

6. **No bounds checks inside the marker cases.**
   `UnpackNext` and `SkipValue` check `pCurrent >= pStart + udiSize`
   at the top of the loop, but length fields (`udiLength` from str 8
   / str 16 / str 32) aren't re-checked against remaining buffer
   before the `MemCpy` / pointer advance. Malformed input →
   out-of-bounds read.

7. **String truncation is silent and inconsistent.**
   `UnpackNext` for `str 32` clamps `udiLength := 255` and continues
   as if nothing happened
   ([UnpackNext.st:141-143](../codesys_code/Application/COMM_FBs/FB_MpUnpacker/UnpackNext.st#L141-L143)).
   No flag, no return code. Caller can't tell a 255-byte string from
   a truncated 10 KB blob. `TryReadString` at least returns `default`
   on overflow — but via `TryReadStringBuffer`, which returns the
   real length. Three layers, three different truncation contracts.

8. **`sLastString : STRING` (255-byte cap) on the FB output.**
   Every string read copies into a single shared member. Reading key
   K1 then key K2 clobbers K1. This is the main reason
   `FindValueByPath` has the temp-unpacker pattern — it can't share
   the main instance's string slot with the caller.

9. **`FB_MpUnpacker` outputs are a mutually-exclusive bag.**
   `diLastDINT`, `rLastREAL`, `sLastString`, `uiLastCount` — only
   one is meaningful at a time, discriminator is `eLastType`.
   Callers that read the wrong one get stale data from a previous
   call, not an error.

10. **Path parser re-parses the string every call.**
    `FIND` / `LEFT` / `MID` / `RIGHT` / `STRING_TO_DINT` inside the
    while loop. Fine for infrequent calls, wasteful when reading a
    20-field record.

### Write side

11. **`FB_MpPacker` is an empty FB.**
    No state, no inputs, no outputs — all methods are stateless
    `POINTER TO BYTE` → `POINTER TO BYTE` transforms. Should be a
    function library (plain `FUNCTION`s) or at minimum documented
    that instantiation is ceremonial. Today you pay an FB instance
    to call what are pure functions.

12. **No compact encoding.**
    `PackDINT` always emits `16#D2` + 4 bytes (5 total), even for
    `diValue = 3` which spec says should be `0x03` (1 byte).
    `PackLINT` always emits 9 bytes. MessagePack requires the
    smallest representation for interop; this wastes bandwidth and
    may confuse strict decoders. Host emits compact; PLC doesn't.

13. **`PackArrayHeader` / `PackMapHeader` cap at 65535.**
    No `16#DD` (array 32) / `16#DF` (map 32) branches. Larger
    containers silently no-op (function returns `undefined`
    POINTER). Same asymmetry as finding #2.

14. **No buffer-size parameter on any `Pack*` method.**
    Every call is "write here, trust the caller." One off-by-one in
    a sequence of Pack calls = stray writes into whatever's next in
    memory. A `pEnd` or `udiRemaining` parameter with return-on-
    exhaustion would catch this at the first overrun.

15. **`PackString` manual strlen with `FOR iLoop := 0 TO 999999`.**
    [PackString.st:35](../codesys_code/Application/COMM_FBs/FB_MpPacker/PackString.st#L35).
    Magic bound; scans up to 1 MB looking for `\0` if the buffer is
    uninitialized memory. Add a length parameter or cap at buffer
    size.

16. **`PackString` doesn't emit `str 32` (`16#DB`).**
    Caps at 65535. Beyond that it returns early without writing.

17. **`SwapREAL.st` is dead code.**
    `PackREAL` uses `Swap32To` directly.

18. **Doc typo in `PackDINT`.**
    Header comment says `16#D2` is "int_64". It's int 32.

### Cross-cutting

19. **No version field, no schema.**
    Referenced by W1 / W3 in
    [`solidification.md`](./solidification.md). Mentioned here for
    completeness — belongs in the envelope, not the lib, but the lib
    has no hook to enforce a version tag on received frames either.

20. **No unit tests.**
    Edits to any of the above are currently "build, run, watch the
    machine." A pack→unpack round-trip test set would be cheap to
    add given these are pure byte transforms.

---

## Ranked fixes

Leverage ÷ risk. Stop-the-world issues first, structural last.

| # | Fix | Risk | Payoff | Status |
|---|---|---|---|---|
| 1 | **Add `map 32` / `array 32` branches to `FindValueByPath`** (finding #2). Mirror the `SkipValue` layout. Without this, any large-container payload is silently unreadable by path. | Low | High | Open |
| 2 | **Add `bin` / `ext` advancement in `UnpackNext`** (finding #5). Even if we don't plan to use them, the parser must not desync on them. Return `MP_UNKNOWN` but advance `pCurrent` past the payload. | Low | High | Open |
| 3 | **Bounds-check length fields** in `UnpackNext` and `SkipValue` (finding #6). One helper `CheckRemaining(pPos, need): BOOL` called before each length-prefixed advance. | Low | High | Open |
| 4 | **Compact integer encoding in `PackDINT` / `PackLINT`** (finding #12). Route through a single `PackInt(LINT)` that picks fixint / int 8 / 16 / 32 / 64. Strict decoders stop misbehaving; bandwidth drops. | Low-medium | Medium | Open |
| 5 | **Add `array 32` / `map 32` and `str 32` to the packers** (finding #13, #16). Symmetric to #1. | Low | Medium | Open |
| 6 | **Unify the marker-dispatch ladder** (finding #1). Extract a `DecodeScalar(pPos, OUT eKind, OUT liValue, OUT rValue, OUT pStr, OUT uiLen, OUT udiAdvance): BOOL` primitive. `TryReadREAL` / `INT64` / `UINT64` become thin casts. `UnpackNext` delegates. One source of truth for marker parsing. | Medium | High | Open |
| 7 | **Consistent truncation/error contract** (finding #7). Pick one: NAK-equivalent (return FALSE and a reason) vs. silent-default. My pick: every `TryRead*` that hit a too-big/wrong-type returns the supplied `default` *and* sets an `eLastError` on the FB the caller can inspect. | Medium | Medium | Open |
| 8 | **Pointer-level key-match in `FindValueByPath`** (finding #3). Replace the temp-FB + string-copy with `KeyEquals(pPos, sKey): BOOL`. Wins map-read cost and removes the reason for finding #9. | Medium | Medium | Open |
| 9 | **Cursor API on maps / arrays** (finding #4). `BeginMap(sPath): BOOL` + `NextField(OUT pKey, OUT pValue): BOOL`. Optional, but the W3 protocol rewrite will appreciate it when we start having records with 10+ fields. | Medium | Medium | Deferred until W3 |
| 10 | **Buffer-size on `Pack*`** (finding #14). Breaking signature change. Do it once, together, with a `TCtx_Packer` record holding `pCur, pEnd, bOverflow`. Chain calls become `ctx.PackDINT(x); ctx.PackString(s)`. | Medium | High (safety) | Open |
| 11 | **Delete dead code + fix typos** (findings #17, #18). | Zero | Readability | Open |
| 12 | **Round-trip test FB** (finding #20). `FB_MsgPackTests` with a dozen pack→unpack→verify cases, runnable from PLC_PRG on demand. Gates every future change. | Low | High | Open |

---

## Ordering

**Phase A — correctness (ship first):** #1, #2, #3, #11.
These are bugs. Low risk, high payoff. Can go in without changing
any signature.

**Phase B — symmetry:** #4, #5.
Matches packer to wire spec and to the reader.

**Phase C — structure:** #6, #7, #8, #10, #12.
Breaking signatures. Do together, behind a round-trip test set (#12
first, then the refactors).

**Phase D — ergonomics:** #9.
Wait until W3 (`protocol.md`) clarifies the record shapes.

---

## Rules for working on this file

- **Don't change the wire format.** Every fix must produce bytes
  the host's `@msgpack/msgpack` already accepts, and must accept
  every byte sequence the host emits.
- **Add the round-trip test (#12) before #6 or #10.** These are the
  risky structural refactors; need a gate.
- **Cycle time is not the acceptance gate here** (unlike renderer
  work). Correctness + bytes-on-wire are.
- **Update this file as items land.** Move completed items to
  "Recently completed" with a one-line summary and date.

---

## Recently completed

_(empty — add entries here as fixes land)_

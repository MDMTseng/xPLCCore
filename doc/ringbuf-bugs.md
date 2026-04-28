# Ring-buffer / TCP-buffer audit

Audit of `GVL.minfo_buf / reMP_info_*` rings and the callsites around
`FB_RingBufferIndex`. The FB itself (head/tail/count wrap math,
push/consume returns) is correct; the holes are all in the usage.
(`aux{0,1,2}_info_buf` removed 2026-04-27 — see #12.)

> **Status sweep 2026-04-27:** all High items closed; #5/#6 closed by POU
> deletion; #7/#8 closed in [TCP_MSGPAK_Server.st](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st);
> #11 closed by SPSC lock-free refactor of FB_RingBufferIndex; **#12
> closed by AUX removal** — `reMP_info_ridx` is now single-producer
> (AxisGroupSM only).
>
> **Status sweep 2026-04-28:** #12 reopened+closed — the post-#11
> "drop-oldest" guard pattern (`IF space()=0 THEN consumeTail()`)
> turned EC_Task into a *second* `_tail` writer alongside the Comm
> task, re-creating an MPSC race on the consumer side. All seven
> EC_Task emission sites converted to drop-newest (skip the write,
> or pack into `GVL.reMP_info_scratch` and decline `pushHead`); see
> #12 below for the full story.
> Remaining open: #3 (closed-with-rationale), #9 (defensive-only, no fix
> needed), #10 (observation).
> See per-item notes for current state.

## High

### 1. 255-byte payload silent truncation — **Closed 2026-04-26**
Both AUX and minfo ingress paths now drop packets with
`fbMyServer.BUFFER_LEN > GVL.MAX_SLOT_PAYLOAD` (255) and bump
`GVL.OverlenDropCount` instead of wrapping via `DINT_TO_BYTE`.
See [TCP_MSGPAK_Server.st:80-82, 136-138](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L80).

### 2. AUX packet dropped silently when ring is full — **Closed 2026-04-26**
AUX path now always emits an ack via the `reMP_info_ridx` reply ring,
with `auxPacketStored` reflecting whether the packet was actually
queued. Drop case yields `{id, ack:false}`; client sees the NAK and can
retry. See [TCP_MSGPAK_Server.st:112-128](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L112).

### 3. `PKT_ALLOW_NEXT` only gates minfo — **Closed-with-rationale 2026-04-27**
[`TCP_MSGPAK_Server.st:58`](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L58)
still gates only on `minfo_buf_ridx`. Acceptable now because #2 is
fixed: AUX-ring-full produces an explicit `ack:false` instead of a
silent drop, so the client surfaces backpressure without depending on
TCP-layer flow control. Reopen if AUX volume increases enough that the
NAK loop becomes load.

### 4. `reMP_info_ridx` overflow + getHead OOB — **Closed 2026-04-27**
All 11 live `getHead()`-then-pushHead callsites in
[AxisGroupSM.st](../codesys_code/Application/APPs/AxisGroupSM.st)
(lines 298, 329, 465, 524, 555, 747, 779, 873, 992, 1046, 1173) are now
preceded by an explicit `IF GVL.reMP_info_ridx.space() = 0 THEN
consumeTail(); GVL.ReMpDropCount := GVL.ReMpDropCount + 1; END_IF`
guard. Twelfth match at [line 913](../codesys_code/Application/APPs/AxisGroupSM.st#L913)
sits inside an `IF FALSE THEN ... END_IF` dead-code block (per repo
convention — see plc.md item #9, "Won't fix").

The matching site in [TCP_MSGPAK_Server.st:116-119](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L116)
has the same guard. Pattern is now codebase-wide convention; see #9
below for the contract callers must follow.

## Medium

### 5. Scan-blocking dead-wait — **Closed 2026-04-24**
`TCP_Server.st` deleted as dead code (was never task-wired in the
current project; the live comm path is `TCP_MSGPAK_Server`).

### 6. Three files consume `minfo_buf_ridx` — **Closed 2026-04-27**
`EthercatPOU.st` and `POU_BUFFER_RUN.st` are gone from the tree. Only
[AxisGroupSM.st](../codesys_code/Application/APPs/AxisGroupSM.st) calls
`minfo_buf_ridx.consumeTail()` now. Verified via grep across
`codesys_code/`.

### 7. Retry-forever while socket xActive=TRUE — **Closed 2026-04-26**
[TCP_MSGPAK_Server.st:173-183](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L173)
drops the head packet after `SEND_MAX_RETRIES` (100) consecutive
failed-Send cycles while `xActive=TRUE`, bumps `GVL.SendStallDropCount`,
and resets the retry counter. xActive=FALSE path drains the entire
backlog (no point replaying stale replies to a future client).

### 8. AUX ack bypasses the retry ring — **Closed 2026-04-26**
AUX ack is now packed into a `reMP_info_ridx` slot like every other
reply, so a transient `Send()` failure gets retried next scan instead
of being lost.
See [TCP_MSGPAK_Server.st:112-128](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L112).

## Defensive

### 9. `getHead() = -1` is never checked at callsites — **Convention, no code change**
Every current caller gates on `space() > 0` first (see #4), so the -1
return path is unreachable. Considered hardening the FB itself
(clamp-to-0 + counter), but rejected: clamping trades a loud PLC fault
(array OOB) for silent corruption of slot[0], which is worse. The
existing -1 makes misuse instantly diagnosable.

**Contract for new callers:** every `getHead()` call MUST be preceded
by either `space() > 0` or an explicit overflow path that
`consumeTail()`s the oldest entry first. Grep
`getHead\(\)` to audit.

### 11. `FB_RingBufferIndex` had a cross-task `_count` race — **Closed 2026-04-27**
The original implementation stored `_count : UINT` and both
`pushHead` (producer task) and `consumeTail` (consumer task)
read-modify-wrote it. EC_Task (prio 0) preempts Comm task (prio 20),
so a `_count := _count + 1` could land between Comm's
`_count := _count - 1` load and store, losing the decrement (or
gaining a phantom item). Window is small (a few CPU instructions per
RMW) which is why the bug never surfaced in production.

**Fix:** dropped `_count` entirely. `size()` is now derived from
`_head`/`_tail`, with each side counting modulo `2 * _capacity` so
empty (`head == tail`) and full (`size == capacity`) are
unambiguous. Each task writes only its own pointer; UINT writes are
atomic on the underlying CPU, so a stale cross-task read produces a
conservative miss (false-full / false-empty) that self-corrects on the
next scan. See [FB_RingBufferIndex.st](../codesys_code/Application/COMM_FBs/FB_RingBufferIndex/FB_RingBufferIndex.st)
header for the full SPSC argument.

`clear()` writes both pointers and is **not** preemption-safe — only
call it from a single-task context. The legacy in-loop `clear()` in
the AxisGroupSM not-Ready drain was already removed when that path
became per-packet NAK.

### 12. `reMP_info_ridx` is MPSC, FB only guarantees SPSC — **Reopened then closed 2026-04-28 (drop-newest)**
First pass (2026-04-27, AUX removal): both `AxisGroupSM` (replies) and
`TCP_MSGPAK_Server` (AUX acks) wrote `reMP_info_ridx`, so the lock-free
SPSC FB (#11) didn't cover the producer side. AUX was a
reserved-but-unused channel (no UI sender, no PLC handler — packets
were just drained), so the entire AUX path was deleted:
`aux{0,1,2}_info_buf[_ridx]` removed from GVL, TCP_MSGPAK_Server
collapsed to the minfo branch, AxisGroupSM's `ProcessAuxCommands`
drain block deleted.

Second pass (2026-04-28): the producer side was indeed clean, but the
**consumer** side wasn't. Item #4's "drop-oldest" guard
(`IF space()=0 THEN consumeTail(); ... END_IF;`) is itself a
`consumeTail` call, and EC_Task (prio 0) was running it inline at
seven emission sites. The Comm task (prio 20) is the legitimate
consumer; EC_Task pre-empting Comm mid-`consumeTail` re-introduced
exactly the MPSC `_tail` race that #11 was meant to remove. Worse,
even when the race was won, EC_Task's overflow path then *wrote* to
`slot[getHead()]` — which is `slot[_tail]` when the ring is full —
corrupting the very packet Comm was trying to send.

**Fix:** drop-newest at every EC_Task emission site. Two patterns:

1. Short pack blocks (NAK/ack stubs): wrap in
   `IF (space() > 0) THEN ... pack ... pushHead(); ELSE
   ReMpDropCount := ReMpDropCount + 1; END_IF;`. The reply is dropped
   entirely on overflow. UI eventually retries via timeout.
2. Long pack blocks (SYS dispatcher): pre-check `space()` once and
   either point `ResponsePacketPointer` at the real ring slot
   (`SlotAvailable := TRUE`) or at the dedicated scratch slot
   (`GVL.reMP_info_scratch`, `SlotAvailable := FALSE`). All `pack`
   calls run unconditionally. At the end, gate `pushHead()` on
   `SlotAvailable`. The scratch buffer prevents trashing the consumer's
   slot while letting the unrolled emit code stay flat.

Sites converted: AxisGroupSM SYS-dispatcher head + tail-pushHead,
missing-type-field NAK, inline group-not-ready NAK,
ProcessMotionPacket head + pushHead, UpdateMotionProgress (MOVE_DONE),
UpdateRuntimeAndInputEvent (ST_CHG, COORD_SET), CheckAxisGroupReady
(group_not_ready drain), ProcessFlyEventsAndIo (TriggerErrorCode,
ACK_SRC_ID).

Verification: `grep reMP_info_ridx\.consumeTail` returns hits only
inside `TCP_MSGPAK_Server.st` (the legitimate Comm-task consumer).
EC_Task is now strictly producer-only on this ring; FB_RingBufferIndex
SPSC contract holds.

If AUX is ever revived, design the new dispatcher with a separate
`aux_reply_ridx` so `reMP_info_ridx` stays SPSC, and keep the new
producer drop-newest as well — never let a producer call
`consumeTail()` on the ring it produces into.

### 10. Ring capacities — **Observation only**
minfo=6, reply=32. 32-slot reply ring measured to absorb a 400-packet
burst with a stalled reader without dropping (see memory
`plc_remp_ring_headroom.md`). 6-slot minfo ingress is tight under
sustained burst but currently fine; revisit if `OverlenDropCount` or
ingress-side queueing pressure ever becomes visible in `GET_DIAG`.

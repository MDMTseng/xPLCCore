# Ring-buffer / TCP-buffer audit

Audit of `GVL.minfo_buf / aux{0,1,2}_info_buf / reMP_info_*` rings and the
callsites around `FB_RingBufferIndex`. The FB itself (head/tail/count wrap
math, push/consume returns) is correct; the holes are all in the usage.

> **Status sweep 2026-04-27:** all High items closed; #5/#6 closed by POU
> deletion; #7/#8 closed in [TCP_MSGPAK_Server.st](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st).
> Remaining open: #3 (closed-with-rationale), #9 (defensive-only, no fix
> needed), #10 (observation). See per-item notes for current state.

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

### 10. Ring capacities — **Observation only**
minfo=6, aux[0..2]=6, reply=32. 32-slot reply ring measured to absorb
a 400-packet burst with a stalled reader without dropping (see memory
`plc_remp_ring_headroom.md`). 6-slot ingress rings are tight under
sustained burst but currently fine; revisit if `OverlenDropCount` or
ingress-side queueing pressure ever becomes visible in `GET_DIAG`.

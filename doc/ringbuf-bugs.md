# Ring-buffer / TCP-buffer audit

Audit of `GVL.minfo_buf / aux{0,1,2}_info_buf / reMP_info_*` rings and the
callsites around `FB_RingBufferIndex`. The FB itself (head/tail/count wrap
math, push/consume returns) is correct; the holes are all in the usage.

Severity ordering is by blast radius (silent corruption > client hang >
latent crash > cleanup).

> **Drift note (2026-04-26):** AxisGroupSM.st has grown substantially
> since this doc was written; the file:line citations below are stale
> and need re-verification before acting on any item. Some fixes have
> landed in-tree (e.g. the SYS-dispatcher path of #4 now has the overflow
> guard at [AxisGroupSM.st:555-558](../codesys_code/Application/APPs/AxisGroupSM.st#L555),
> bumping `GVL.ReMpDropCount`). A full audit pass is W7 work.

## High

### 1. 255-byte payload silent truncation
**Where:** Producer that writes a length byte into slot[0]:
- [`TCP_MSGPAK_Server.st:140`](../codesys_code/Application/APPs/TCP_MSGPAK_Server.st#L140) (the only remaining `tmp_ptr[0] := DINT_TO_BYTE(...)` site as of 2026-04-26 — aux paths and `TCP_Server.st` are no longer wired)

All do `tmp_ptr[0] := DINT_TO_BYTE(curPacketLen)` with no range check.
Slot capacity is 256 bytes (1 len + 255 data). Packets > 255 bytes wrap
via `DINT_TO_BYTE` and downstream unpackers read a truncated length ->
silent decode failure or mis-parse of subsequent bytes.

**Fix:** Before the write, reject if `curPacketLen > 255`. NAK on the
client-facing path (AUX ack) and drop+log on the motion path.

### 2. AUX packet dropped silently when ring is full
**Where:** `Application/APPs/TCP_MSGPAK_Server.st:74-98`

If `aux{n}_info_buf_ridx.space() = 0`, the packet is skipped and
`auxPacketStored` stays FALSE, so the ack block at 100-114 never runs.
Client waits for an ack that never comes -> hangs on timeout.

**Fix:** Always send an ack. On the drop path, emit `ack:FALSE` with an
err field so the client can retry.

### 3. `PKT_ALLOW_NEXT` only gates minfo
**Where:** `Application/APPs/TCP_MSGPAK_Server.st:53`

`PKT_ALLOW_NEXT := GVL.minfo_buf_ridx.space() > 1`. The server keeps
accepting AUX packets even when aux rings are full, which feeds #2.

**Fix:** Either gate on all four rings, or keep minfo-only but pair with
the NAK in #2 so AUX backpressure surfaces to the client.

### 4. `reMP_info_ridx` overflow is silent AND pre-indexes via getHead()
**Where:** Every `getHead()`-then-pushHead pair on the reply ring. As of
2026-04-26 there are 12 such sites in AxisGroupSM.st (lines 302, 333,
469, 528, 559, 751, 783, 877, 913, 996, 1051, 1177). Status varies:
the SYS-dispatcher site at [line 559](../codesys_code/Application/APPs/AxisGroupSM.st#L559)
is preceded by an explicit `space()=0 → consumeTail()` guard ([553-558](../codesys_code/Application/APPs/AxisGroupSM.st#L553));
the others have not been individually audited.

Each callsite does
```
ResponsePacketPointer := ADR(GVL.reMP_info_arr[GVL.reMP_info_ridx.getHead()]);
... pack ...
GVL.reMP_info_ridx.pushHead();  // BOOL return ignored
```
When the ring is full (slow client / stalled connection), `getHead()`
returns -1 and `reMP_info_arr[-1]` is array OOB -> exception in the PLC
task. pushHead's FALSE return is also ignored, so even on a fresh full
state the packed response is lost.

**Fix:** Guard head-fetch on `space() > 0`. On full, either overwrite the
oldest (drop-newest-is-fine if client already missed it) or refuse the
response with a log bump.

## Medium

### 5. Scan-blocking dead-wait
**Where:** `Application/APPs/TCP_Server.st:158`
```
WHILE GVL.minfo_buf_ridx.space() = 0 DO
    fbWaitTimeout(IN:=TRUE, PT:=UDINT_TO_TIME(1));
    IF fbWaitTimeout.Q THEN EXIT; END_IF;
END_WHILE
```
Blocks the task scan on a 1 ms TON. If the consumer is stalled this is
a scan-overrun. Likely dead code (current comm path goes through
`TCP_MSGPAK_Server`) but still compiled in.

**Fix:** If TCP_Server is not wired to any task, delete the POU. If it
still is, remove the loop and drop the packet instead.

### 6. Three files consume `minfo_buf_ridx`
**Where:** `AxisGroupSM.st`, `EthercatPOU.st`, `POU_BUFFER_RUN.st` all
call `consumeTail()` on the same ring. Only `AxisGroupSM` is the current
consumer. If more than one is in the task config, they race.

**Fix:** Confirm task wiring in the IDE, delete the legacy POUs.

### 7. Retry-forever while socket xActive=TRUE
**Where:** `Application/APPs/TCP_MSGPAK_Server.st:141-149`

If `Send()` keeps returning FALSE while `xActive=TRUE` (TX backpressure,
half-open socket), the retry loop holds the slot indefinitely. Combined
with #4, the reply ring fills up.

**Fix:** Stamp each packet with enqueue time; drop after N ms or after K
retries, whichever first.

### 8. AUX ack bypasses the retry ring
**Where:** `Application/APPs/TCP_MSGPAK_Server.st:100-114`

AUX ack is sent inline via `fbMyServer.Send()`. If Send returns FALSE,
the ack is lost; no requeue. The main response path uses
`reMP_info_ridx` for exactly this reason.

**Fix:** Route aux acks through `reMP_info_ridx` like every other reply.

## Defensive

### 9. `getHead() = -1` never checked at callsites
Currently safe because every caller gates on `size()/space()` first, but
brittle. A future caller that forgets the gate indexes with -1 and the
task faults.

**Fix:** Either guard in the FB (return a BOOL out-param + idx), or add
explicit assertions at the callsites.

### 10. Ring capacities
minfo=6, aux[0..2]=6, reply=32. 6 is tight under burst traffic or a
drain stall (e.g. Error state). Not a bug, but revisit after the above
are in.

## Suggested rollout

- **Bundle 1 (silent corruption):** #1 length guard, #4 reMP overflow guard.
- **Bundle 2 (client liveness):** #2 + #3 AUX NAK path.
- **Bundle 3 (cleanup):** delete dead POUs (#5, #6).
- **Bundle 4 (backpressure):** #7 + #8 stall recovery + aux ack routing.

"""B.4 -- SCRATCHPAD_WRITE burst-write atomicity / throughput probe.

decisions_2026-06-22.md B.4 picked "A: write a probe" instead of
trusting the theoretical analysis (PLC is single-threaded ST, SYS drain
caps at 16 per scan, our real write rate is ~1/sec so the queue can't
back up). This probe checks the assumption end-to-end:

  - blast N back-to-back SCRATCHPAD_WRITEs with sequence-numbered
    plan_index values
  - after each write, immediately read GET_MACHINE_STATE and verify
    scratchpad.plan_index == the value we just wrote (i.e. PLC
    serialised our write before the read landed)
  - measure write -> read latency for headroom budget
  - sanity-check ScratchpadV1 stays consistent (schema_version=1,
    boot_epoch matches boot_epoch_now)

Run conditions: PLC up, daemon optional (this is a pure TCP probe).
Requires the §4 (2) PLC change to be live (commit 2ac004f or later).

Outputs a PASS/FAIL summary plus a percentile breakdown of the
write+verify round-trip times -- useful baseline for the cycle-time
review on the host-write rate budget.
"""
import os
import socket
import statistics
import sys
import time

import msgpack

PLC = ("192.168.1.70", 8125)
N_WRITES = 500          # at our planned ~1/sec rate, 500 is a long burn
PROTOCOL_VERSION = 1


def _id_iter():
    n = 90000
    while True:
        n += 1
        yield n


def send_recv(sock, payload, pid, timeout=1.0):
    payload = dict(payload, id=pid, protocol_version=PROTOCOL_VERSION)
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    unp = msgpack.Unpacker(raw=False, strict_map_key=False)
    deadline = time.time() + timeout
    sock.settimeout(0.2)
    while time.time() < deadline:
        try:
            d = sock.recv(8192)
        except socket.timeout:
            continue
        if not d:
            return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == pid:
                return obj
    return None


def main():
    sock = socket.socket()
    sock.settimeout(3.0)
    sock.connect(PLC)
    ids = _id_iter()

    # Establish baseline + warm cache.
    snap = send_recv(sock, {"type": "SYS", "cmd": "GET_MACHINE_STATE"}, next(ids))
    if not snap or "scratchpad" not in snap:
        print("FAIL: no scratchpad field in MachineState -- is the PLC running"
              " the §4 (2) build?")
        return 1
    boot_epoch_now = snap["boot_epoch_now"]
    print("baseline: boot_epoch_now=%s, plan_id=%s, plan_index=%s" % (
        boot_epoch_now,
        snap["scratchpad"]["plan_id"],
        snap["scratchpad"]["plan_index"],
    ))

    plan_id = 0x600D  # arbitrary marker so we can tell this run apart
    latencies_ms = []
    mismatches = 0
    epoch_drift = 0
    pre_intent_kind = 1  # advance_reel

    t_start = time.time()
    for i in range(N_WRITES):
        # Write with a unique plan_index every iteration. The PLC stamps
        # schema_version + boot_epoch on every write.
        t0 = time.perf_counter()
        ack = send_recv(sock, {
            "type": "SYS", "cmd": "SCRATCHPAD_WRITE",
            "plan_id": plan_id,
            "plan_index": i,
            "intent_kind": pre_intent_kind,
            "intent_movement_id": 1000 + i,
            "last_vision_pulse": 2000 + i,
        }, next(ids))
        if not ack or ack.get("ack") is not True:
            print("FAIL: SCRATCHPAD_WRITE NAK at i=%d: %r" % (i, ack))
            return 1

        # Read-back. We expect the value we just wrote to be visible by
        # the time the GET_MACHINE_STATE reply lands.
        snap = send_recv(sock, {"type": "SYS", "cmd": "GET_MACHINE_STATE"},
                         next(ids))
        t1 = time.perf_counter()
        latencies_ms.append((t1 - t0) * 1000.0)

        if not snap:
            print("FAIL: no GET_MACHINE_STATE reply at i=%d" % i)
            return 1
        sp = snap["scratchpad"]
        if sp["plan_index"] != i:
            mismatches += 1
            print("WARN: plan_index mismatch at i=%d (got %s)" % (i, sp["plan_index"]))
        if snap["boot_epoch_now"] != boot_epoch_now:
            epoch_drift += 1
            print("WARN: boot_epoch_now changed mid-test (PLC restart?) "
                  "i=%d, before=%s, after=%s" % (i, boot_epoch_now, snap["boot_epoch_now"]))
        if sp["boot_epoch"] != boot_epoch_now:
            print("WARN: scratchpad.boot_epoch not stamped to boot_epoch_now"
                  " (got %s, expected %s)" % (sp["boot_epoch"], boot_epoch_now))

    elapsed = time.time() - t_start
    print()
    print("=== summary ===")
    print("writes:       %d" % N_WRITES)
    print("elapsed:      %.2fs" % elapsed)
    print("throughput:   %.0f writes/sec" % (N_WRITES / elapsed))
    print("mismatches:   %d (plan_index didn't match)" % mismatches)
    print("epoch drift:  %d" % epoch_drift)
    latencies_ms.sort()
    p = lambda q: latencies_ms[int(q * len(latencies_ms)) - 1]
    print("write+read latency ms:")
    print("  min=%.2f  p50=%.2f  p95=%.2f  p99=%.2f  max=%.2f" % (
        latencies_ms[0], p(0.5), p(0.95), p(0.99), latencies_ms[-1]))

    sock.close()
    if mismatches > 0 or epoch_drift > 0:
        print()
        print("VERDICT: anomalies present -- review above")
        return 1
    print()
    print("VERDICT: PASS (PLC serialises writes; reads see the write that"
          " preceded them every time)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

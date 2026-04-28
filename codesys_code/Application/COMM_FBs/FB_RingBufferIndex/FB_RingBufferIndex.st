(*
    Function Block: FB_RingBufferIndex
    Description:    Manages the indices of a ring (circular) buffer.
                    This FB does not store any data itself, but rather provides the correct
                    indices for an external data buffer array.

    Behavior:
    - 'Head' (H): The index where the NEXT new element should be written.
    - 'Tail' (T): The index of the OLDEST element that can be read.

    Visualization:
    | . . . . . T . . . . . H . . . . . |
                ^           ^
                |           |
             Oldest Data   Next Write Slot

    SPSC lock-free safety (added 2026-04-27):
    - Producer task is the only writer of _head (via pushHead).
    - Consumer task is the only writer of _tail (via consumeTail).
    - size() / space() are *derived* from _head and _tail; no _count
      RMW that both sides would race on.
    - _head / _tail count modulo (2 * _capacity) to disambiguate empty
      from full without sacrificing a slot. Slot index is recovered as
      (_head MOD _capacity) / (_tail MOD _capacity).
    - UINT reads/writes are single-instruction on the underlying CPU
      (16-bit aligned), so a stale cross-task read produces a
      conservative miss (false-full at the producer, false-empty at the
      consumer) that self-corrects on the next scan.
    - clear() writes both _head and _tail and is therefore NOT
      preemption-safe; only call it from a single-task context (init or
      offline-only), never from inside a producer/consumer loop.

    NOT MPSC-safe: if two tasks both push (or both consume) on the same
    instance, getHead() can return the same slot to both producers and
    pushHead() can race on _head. In this project reMP_info_ridx is
    pushed only by EC_Task (AxisGroupSM family) and consumed only by
    Comm task (TCP_MSGPAK_Server) -- a clean SPSC arrangement. Earlier
    revisions had EC_Task perform consumeTail() on overflow ("drop-
    oldest") which created a second consumer racing the Comm task; that
    has been replaced everywhere with a "drop-newest" pattern (skip the
    write if space()=0, or pack into GVL.reMP_info_scratch and decline
    to pushHead). See doc/ringbuf-bugs.md item #12.

    Author:         Gemini (initial), refactored to SPSC lock-free 2026-04-27
    Version:        2.0
*)
FUNCTION_BLOCK FB_RingBufferIndex
VAR_INPUT
END_VAR
VAR_OUTPUT
END_VAR
VAR
    // Internal state variables. _head is producer-owned; _tail is
    // consumer-owned. Each is updated by a single task only.
    _head           : UINT := 0;   // Next write position, mod _cap2.
    _tail           : UINT := 0;   // Next read position,  mod _cap2.
    _capacity       : UINT := 0;   // User-visible capacity (set by init()).
    _cap2           : UINT := 0;   // _capacity * 2, precomputed for size math.
END_VAR
(* =========== IMPLEMENTATION =========== *)

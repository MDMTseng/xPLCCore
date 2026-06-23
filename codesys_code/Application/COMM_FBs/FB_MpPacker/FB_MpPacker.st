FUNCTION_BLOCK FB_MpPacker
VAR_INPUT
END_VAR
VAR_OUTPUT
END_VAR
VAR
    // Builder-pattern slot binding. Caller invokes BindSlot(p) once per
    // reply/event acquisition (TryAcquireReplySlot / ProbeReplySlot /
    // TryAcquireReplySlotOrScratch on AxisGroupSM all auto-bind), then
    // the high-level KV/Pair/Elem helpers below use this stashed pointer
    // instead of taking the slot as a parameter at every call.
    //
    // Default 0 (NULL) -- a Pack* call before BindSlot will deref null
    // and crash deterministically; better than silently writing into
    // a stale slot. Re-bind on every acquire to make stale impossible.
    pSlot : POINTER TO MsgPakInfo := 0;

    // Overflow latch (set by any PackKv*/PackPair*/PackElem*/PackKvMap/
    // PackKvArray that observes whead crossing buf_capacity after its
    // write). Reset by BindSlot. Once latched, subsequent helper calls
    // early-out (return FALSE without writing) -- prevents continued
    // overruns from chewing through the GVL.reMP_info_buf_guard region.
    //
    // Public on purpose: callers that want to react (e.g. emit
    // err='reply_overflow' on the WIRE rather than letting Wrapup
    // silently drop the whole slot) can poll MessagePackerFb.bSlotOverflowed.
    // Most don't need to -- Wrapup's own post-hoc check still detects
    // and refuses to push, GVL.ReMpOverflowDropCount still ticks.
    bSlotOverflowed : BOOL := FALSE;
END_VAR
(* =========== IMPLEMENTATION =========== *)

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
END_VAR
(* =========== IMPLEMENTATION =========== *)

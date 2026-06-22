(* =========== IMPLEMENTATION =========== *)
// One-shot allocation of the reply-ring MsgPakInfo headers. Runs on the
// very first scan only (RuntimeMs = 0); on subsequent scans the IF
// short-circuits and this is essentially free.
IF RuntimeMs = 0 THEN
    FOR Index := 0 TO GVL.reMP_info_ridx.capacity() - 1 DO
        GVL.reMP_info_arr[Index] := MsgPakInfoInit(
            buff := ADR(GVL.reMP_info_buf[Index]),
            buffer_size := GVL.reMP_info_buff_SIZE,
            useLargeElementCountField := TRUE
        );
    END_FOR
    // Scratch slot for ring-full drop-newest fallback (see GVL note).
    GVL.reMP_info_scratch := MsgPakInfoInit(
        buff := ADR(GVL.reMP_info_scratch_buf),
        buffer_size := GVL.reMP_info_buff_SIZE,
        useLargeElementCountField := TRUE
    );
END_IF;

// Boot-epoch bump (§4 (2) scratchpad). AppBootHandled is non-retained
// so it defaults FALSE on every cold/warm start; we set it TRUE here
// after incrementing the retained counter, so this fires once per
// application boot. Renderer correlates ScratchpadV1.BootEpoch vs
// GVL.BootEpochCount to detect "PLC restarted since my last write".
IF NOT GVL.AppBootHandled THEN
    GVL.BootEpochCount := GVL.BootEpochCount + 1;
    // SchemaVersion=0 means "cold-reset wiped" -- PLC stamps 1 so the
    // renderer can distinguish "PLC never had any scratchpad" from
    // "renderer never wrote one yet on this fresh-init PLC". The
    // remaining fields stay 0 (host fills them on first write).
    IF GVL.ScratchpadV1.SchemaVersion = 0 THEN
        GVL.ScratchpadV1.SchemaVersion := 1;
        GVL.ScratchpadV1.BootEpoch := GVL.BootEpochCount;
    END_IF;
    GVL.AppBootHandled := TRUE;
END_IF;

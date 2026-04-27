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
END_IF;

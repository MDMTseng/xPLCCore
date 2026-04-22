METHOD ProcessLengthBytes : BOOL
VAR_INPUT
END_VAR
VAR
    ui_shift : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)

(* Accumulate length bytes in big-endian format *)
ui_shift := (ui_length_bytes_remaining - 1) * 8;
ui_length_value := ui_length_value OR (SHL(TO_UDINT(by_current_byte), ui_shift));
ui_length_bytes_remaining := ui_length_bytes_remaining - 1;

IF ui_length_bytes_remaining = 0 THEN
    (* Length complete - optimized type dispatch *)
    (* Check for arrays first *)
    IF (by_last_type_byte = 16#DC) OR (by_last_type_byte = 16#DD) THEN
        (* Array 16 or Array 32 *)
        IF ui_length_value > 0 THEN
            PushStack(ui_length_value);
        ELSE
            DecrementStack();
        END_IF
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    ELSIF (by_last_type_byte = 16#DE) OR (by_last_type_byte = 16#DF) THEN
        (* Map 16 or Map 32 *)
        IF ui_length_value > 0 THEN
            PushStack(ui_length_value * 2);
        ELSE
            DecrementStack();
        END_IF
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    ELSIF (by_last_type_byte = 16#C7) OR (by_last_type_byte = 16#C8) OR (by_last_type_byte = 16#C9) THEN
        (* Ext 8/16/32 *)
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        ui_data_bytes_remaining := ui_length_value;
    ELSE
        (* String or binary (C4, C5, C6, D9, DA, DB) *)
        ui_data_bytes_remaining := ui_length_value;
        IF ui_data_bytes_remaining > 0 THEN
            e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        ELSE
            (* Empty string/binary *)
            DecrementStack();
            e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
        END_IF
    END_IF
END_IF
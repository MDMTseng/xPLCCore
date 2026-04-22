METHOD HandleHeaderByte : INT
VAR_INPUT
END_VAR
VAR
    by_type : BYTE;
    ui_count : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)

by_type := by_current_byte;

(* Optimized: Check most common types first (FixInt covers ~50% of all values) *)
(* FixInt: positive (0x00-0x7F) and negative (0xE0-0xFF) *)
IF (by_type <= 16#7F) OR (by_type >= 16#E0) THEN
    DecrementStack();
    e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    RETURN;
END_IF

(* FixMap (0x80-0x8F: 0-15 pairs) - common container type *)
IF (by_type >= 16#80) AND (by_type <= 16#8F) THEN
    ui_count := by_type - 16#80;
    IF ui_count > 0 THEN
        PushStack(ui_count * 2);
    ELSE
        DecrementStack();
    END_IF
    e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    RETURN;
END_IF

(* FixArray (0x90-0x9F: 0-15 elements) - common container type *)
IF (by_type >= 16#90) AND (by_type <= 16#9F) THEN
    ui_count := by_type - 16#90;
    IF ui_count > 0 THEN
        PushStack(ui_count);
    ELSE
        DecrementStack();
    END_IF
    e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    RETURN;
END_IF

(* FixStr (0xA0-0xBF: 0-31 bytes) - common string type *)
IF (by_type >= 16#A0) AND (by_type <= 16#BF) THEN
    ui_count := by_type - 16#A0;
    IF ui_count > 0 THEN
        ui_data_bytes_remaining := ui_count;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
    ELSE
        DecrementStack();
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
    END_IF
    RETURN;
END_IF

(* Use CASE for exact byte matches - more efficient than IF chain *)
CASE by_type OF
    16#C0:  (* nil *)
        DecrementStack();
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
        
    16#C2:  (* false *)
        DecrementStack();
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
        
    16#C3:  (* true *)
        DecrementStack();
        e_state := E_MsgPakLevelParser_STATE.EXPECT_HEADER;
        
    16#C4:  (* bin 8 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 1;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#C5:  (* bin 16 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 2;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#C6:  (* bin 32 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 4;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#C7:  (* ext 8 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 1;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#C8:  (* ext 16 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 2;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#C9:  (* ext 32 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 4;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#CA:  (* float 32 *)
        ui_data_bytes_remaining := 4;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#CB:  (* float 64 *)
        ui_data_bytes_remaining := 8;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#CC:  (* uint 8 *)
        ui_data_bytes_remaining := 1;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#CD:  (* uint 16 *)
        ui_data_bytes_remaining := 2;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#CE:  (* uint 32 *)
        ui_data_bytes_remaining := 4;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#CF:  (* uint 64 *)
        ui_data_bytes_remaining := 8;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#D0:  (* int 8 *)
        ui_data_bytes_remaining := 1;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#D1:  (* int 16 *)
        ui_data_bytes_remaining := 2;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#D2:  (* int 32 *)
        ui_data_bytes_remaining := 4;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#D3:  (* int 64 *)
        ui_data_bytes_remaining := 8;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_DATA;
        
    16#D4:  (* fixext 1 *)
        by_last_type_byte := by_type;
        ui_data_bytes_remaining := 1;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        
    16#D5:  (* fixext 2 *)
        by_last_type_byte := by_type;
        ui_data_bytes_remaining := 2;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        
    16#D6:  (* fixext 4 *)
        by_last_type_byte := by_type;
        ui_data_bytes_remaining := 4;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        
    16#D7:  (* fixext 8 *)
        by_last_type_byte := by_type;
        ui_data_bytes_remaining := 8;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        
    16#D8:  (* fixext 16 *)
        by_last_type_byte := by_type;
        ui_data_bytes_remaining := 16;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_EXT_TYPE;
        
    16#D9:  (* str 8 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 1;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DA:  (* str 16 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 2;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DB:  (* str 32 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 4;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DC:  (* array 16 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 2;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DD:  (* array 32 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 4;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DE:  (* map 16 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 2;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    16#DF:  (* map 32 *)
        by_last_type_byte := by_type;
        ui_length_bytes_remaining := 4;
        ui_length_value := 0;
        e_state := E_MsgPakLevelParser_STATE.EXPECT_LENGTH;
        
    ELSE
        (* Invalid byte - not a valid MessagePack type *)
        e_error := E_MsgPakLevelParser_ERROR.UNEXPECTED_BYTE;
END_CASE
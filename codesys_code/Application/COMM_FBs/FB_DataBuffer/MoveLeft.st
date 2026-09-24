METHOD MoveLeft : BOOL
VAR_INPUT
	udiIndex : DINT;
END_VAR
VAR_INST
    udiBytesToMove : DINT;
    diByte         : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    // Check if the consumption index is valid.
    IF udiIndex > 0 AND udiIndex <= udiCurrentSize THEN
        // Calculate the number of bytes remaining after the consumption index.
        udiBytesToMove := udiCurrentSize - udiIndex;
        
        // Shift the rest to the front. Source and destination overlap, and
        // MemCpy does not promise to handle that (review 2026-09-24): copy
        // forward byte by byte, which is correct for a move to the left.
        FOR diByte := 0 TO udiBytesToMove - 1 DO
            pBuffer[diByte] := pBuffer[diByte + udiIndex];
        END_FOR
        
        
        // Update the new size of the buffer.
        udiCurrentSize := udiBytesToMove;
    END_IF
END_IF
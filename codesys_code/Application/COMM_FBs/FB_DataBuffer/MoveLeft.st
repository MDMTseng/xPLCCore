METHOD MoveLeft : BOOL
VAR_INPUT
	udiIndex : DINT;
END_VAR
VAR_INST
    udiBytesToMove : DINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
IF pBuffer <> 0 THEN
    // Check if the consumption index is valid.
    IF udiIndex > 0 AND udiIndex <= udiCurrentSize THEN
        // Calculate the number of bytes remaining after the consumption index.
        udiBytesToMove := udiCurrentSize - udiIndex;
        
        // Use MEMMOVE to shift the data.
        //MEM.MemMove(pBuffer + udiIndex, pBuffer, UDINT_TO_UINT(udiBytesToMove));//memory overlap is allowed
		
		MEMUtils.MemCpy(pBuffer, pBuffer + udiIndex, UDINT_TO_UINT(udiBytesToMove));
        
        
        // Update the new size of the buffer.
        udiCurrentSize := udiBytesToMove;
    END_IF
END_IF
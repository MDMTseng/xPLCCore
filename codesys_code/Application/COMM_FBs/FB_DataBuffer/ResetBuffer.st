METHOD ResetBuffer : BOOL
VAR_INPUT
	p_sBuffer : POINTER TO BYTE; (* IN: The address of the external buffer array *)
    udi_Capacity : DINT   ;      (* IN: The total capacity of the external buffer *)
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Store the external buffer's address and capacity internally.
pBuffer := p_sBuffer;
udiCapacity := udi_Capacity;

// Reset the buffer's internal state.
udiCurrentSize := 0;
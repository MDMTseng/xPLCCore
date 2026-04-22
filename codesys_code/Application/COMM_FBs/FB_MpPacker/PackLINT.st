(*
    This method serializes a 64-bit integer (LINT) into a byte stream in Big-Endian format.
    It first writes a type marker (16#D3) and then the 8-byte integer value.
    It returns a pointer to the next available position in the buffer.
*)
METHOD PackLINT : POINTER TO BYTE
VAR_INPUT
    pStart  : POINTER TO BYTE; // Input: Pointer to the start of the buffer segment for writing
    liValue : LINT;          // Input: The 64-bit integer value to pack
END_VAR
(* =========== IMPLEMENTATION =========== *)

    // Step 1: Write the type marker (1 byte)
    // Using 16#D3, which is the code for a 64-bit signed integer in the MessagePack format.
    pStart^ := 16#D3;

    // Step 2: Get the Big-Endian representation of the LINT value
    // On a Little-Endian system (like most PLCs), this requires swapping the byte order.
	Swap64To(ADR(liValue),pStart+1);


    // Step 4: Return the new pointer position
    // The new position is the start + 1 (for the marker) + 8 (for the LINT data).
    PackLINT := pStart + 1+8;
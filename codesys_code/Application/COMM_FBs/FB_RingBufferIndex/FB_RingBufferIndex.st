(*
    Function Block: FB_RingBufferIndex
    Description:    Manages the indices of a ring (circular) buffer.
                    This FB does not store any data itself, but rather provides the correct
                    indices for an external data buffer array.

    Behavior:
    - 'Head' (H): The index where the NEXT new element should be written.
    - 'Tail' (T): The index of the OLDEST element that can be read.

    Visualization:
    | . . . . . T . . . . . H . . . . . |
                ^           ^
                |           |
             Oldest Data   Next Write Slot

    - Empty State: The internal count of elements is 0.
    - Full State:  The internal count of elements equals the buffer's capacity.

    Author:         Gemini
    Version:        1.0
*)
FUNCTION_BLOCK FB_RingBufferIndex
VAR_INPUT
END_VAR
VAR_OUTPUT
END_VAR
VAR
    // Internal state variables
    _head           : UINT:=0;   // Index for the next write operation
    _tail           : UINT:=0;   // Index for the next read operation
    _capacity       : UINT:=0;   // Total size of the buffer, set by init()
    _count          : UINT:=0;   // Current number of items in the buffer
END_VAR
(* =========== IMPLEMENTATION =========== *)

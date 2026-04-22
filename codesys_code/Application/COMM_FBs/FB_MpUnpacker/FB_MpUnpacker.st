(*
================================================================================
 FB_MpUnpacker: MessagePack Unpacker/Parser with Path Query
================================================================================
This function block parses a MessagePack byte stream.

Instructions:
1. Create a Function Block named 'FB_MpUnpacker'.
2. Copy the main variable declarations into its declaration part.
3. Add each METHOD to the FB, copying the respective variable declarations
   and implementation code.

New Feature: Path Querying
- You can now directly read a value using a path string.
- Use dot notation for nested objects: 'obj.key'
- Use brackets for array indices: 'arr[0]'
- Example: 'data.measurements[2].value'

Key Feature:
- Alignment-Safe: Uses MEMUtils.MemCpy for reading DINT and REAL values
  to prevent memory access violation errors on sensitive hardware.
*)
FUNCTION_BLOCK FB_MpUnpacker
VAR_INPUT
END_VAR
VAR_OUTPUT
	eLastType 		: E_MpType;
	diLastDINT 		: DINT;
	rLastREAL 		: REAL;
	sLastString 	: STRING;
	uiLastCount 	: UINT;
END_VAR
VAR CONSTANT
	MAX_NESTING_DEPTH : INT := 16;
END_VAR
VAR
	pStart 			: POINTER TO BYTE;
	pCurrent 		: POINTER TO BYTE;
	udiSize 		: UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)

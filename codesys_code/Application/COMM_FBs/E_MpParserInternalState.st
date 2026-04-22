(* Internal states for byte-by-byte parsing *)
TYPE E_MpParserInternalState :
(
	STATE_IDLE, 			// Waiting for first byte of a new item
	STATE_READING_LENGTH, 	// Reading length/count bytes
	STATE_READING_DATA 		// Reading actual payload data (skipping it)
);
END_TYPE
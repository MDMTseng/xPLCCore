TYPE T_NestingContext :
STRUCT
	bIsMap 		: BOOL; // TRUE if this level is a map, FALSE if it's an array
	uiItemsLeft : UINT; // Number of items remaining to be skipped at this level
END_STRUCT
END_TYPE
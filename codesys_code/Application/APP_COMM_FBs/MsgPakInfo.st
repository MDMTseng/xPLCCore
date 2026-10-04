TYPE MsgPakInfo :
STRUCT
	buf_capacity:UDINT;
	buf_size:UDINT;
	buf:POINTER TO BYTE;
	
	whead:POINTER TO BYTE;
	
	
	useLargeElementCountField:BOOL;
	ele_count:UDINT;
	// Set by the FB_MpPacker primitives when a write would not fit in
	// buf (nothing is written then); MsgPakInfoWrapup refuses the
	// packet. Cleared by MsgPakInfoInit / Clear. 2026-10-04.
	overflowed:BOOL;
	
END_STRUCT
END_TYPE

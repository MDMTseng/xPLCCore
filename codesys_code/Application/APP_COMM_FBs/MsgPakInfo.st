TYPE MsgPakInfo :
STRUCT
	buf_capacity:UDINT;
	buf_size:UDINT;
	buf:POINTER TO BYTE;
	
	whead:POINTER TO BYTE;
	
	
	useLargeElementCountField:BOOL;
	ele_count:UDINT;
	
END_STRUCT
END_TYPE

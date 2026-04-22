{attribute 'qualified_only'}
VAR_GLOBAL
	
	
	minfo_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	minfo_buf_ridx:FB_RingBufferIndex(6);
	
	
	aux0_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux0_info_buf_ridx:FB_RingBufferIndex(6);
	aux1_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux1_info_buf_ridx:FB_RingBufferIndex(6);
	aux2_info_buf: ARRAY [0..5] OF ARRAY [0..256-1]OF BYTE; 
	aux2_info_buf_ridx:FB_RingBufferIndex(6);
	
	reMP_info_SIZE:INT:=32;
	reMP_info_buff_SIZE:INT:=256;
	reMP_info_buf: ARRAY [0..32-1] OF ARRAY [0..256-1]OF BYTE; 
	reMP_info_arr: ARRAY [0..32-1] OF MsgPakInfo; 
	reMP_info_ridx:FB_RingBufferIndex(32);
	
	
	
	
END_VAR

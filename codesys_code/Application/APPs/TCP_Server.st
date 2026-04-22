PROGRAM TCP_Server
VAR CONSTANT
    // --- Define the maximum size of the incoming and outgoing data ---
    mySize : UDINT := 1024;

    // --- Prefix to add to incoming data as a BYTE array (currently unused in echo logic) ---
    // ASCII values for "echo:" (e=16#65, c=16#63, h=16#68, o=16#6F, :=16#3A)
    //cEchoPrefix : ARRAY [0..4] OF BYTE := [16#65, 16#63, 16#68, 16#6F, 16#3A];
    LEN_ECHO_PREFIX : UDINT := 5; // Length of the "echo:" prefix

    // --- String "HR" to send periodically ---
    cHRString : ARRAY [0..1] OF BYTE := [16#48, 16#52]; // ASCII for "HR"
    LEN_HR_STRING : UDINT := 2; // Length of "HR"
END_VAR

VAR
    // --- Server and Connection Function Blocks ---
    myIPAddress : NBS.IPv4Address := (ipAddress:='192.168.3.6');
    myServer : NBS.TCP_Server := (
        itfIPAddress:=myIPAddress,
        uiPort:=8123,
		xEnable:=TRUE,
		udiMaxConnections:=1
    );
    itfServer : NBS.IServer;
    xServerActive : BOOL;

    myConnection : NBS.TCP_Connection;
    xEnableConnection : BOOL; // Controls the actual enable input of myConnection

    // --- Data Buffers and Counters ---
    udiBytesReceived : UDINT;
    eReadErrorID, eWriteErrorID : NBS.ERROR;
    sSendBuffer : ARRAY [0..mySize + LEN_ECHO_PREFIX - 1] OF BYTE; // BYTE array for outgoing data (sized for prefix + data)
    udiBytesToSend : UDINT; // Actual number of bytes to send in the current cycle

    // --- State Control for Connection Management ---
    xResetConnection : BOOL := FALSE;      // Flag to initiate a connection reset process
    xLastConnectionActive : BOOL := FALSE; // Holds the state of xActive from the previous cycle

    // --- Delay Timer for Connection Reset ---
    fbResetDelayTimer : Standard.TON;      // TON function block for the delay
    xDelayDone : BOOL;                     // Output of the TON timer

	fb_MsgPakParser : FB_MsgPakLevelParser;
	
	
	
	bufferbuffer: ARRAY [0..1024-1] OF BYTE; 
	dbuffer:FB_DataBuffer;
	i : DINT;
	curPacketStartIdx:DINT;
	curPacketLen:DINT;
	TMP_buffer_ptr : POINTER TO BYTE;
	TMP_buff_size:DINT:=0;
	
	
	fb_MsgUnPak : FB_MpUnpacker;
	data_type:STRING;
	cmd_type:STRING;
	//motionPose:SM3_Robotics.SMC_POS_REF:= ( c := (x := 00.0, y := 0.0, z := 0.0, a := 0.0));
	
	
	tmp_ptr:POINTER TO BYTE;
	

	INIT:BOOL:=TRUE;
	
	
	
	fbWaitTimeout   : TON;         // Timer to escape the blocking loop
	
END_VAR

(* =========== IMPLEMENTATION =========== *)

IF INIT THEN
	fb_MsgPakParser.ResetParser();
	dbuffer.reset_buffer(ADR(bufferbuffer[0]),SIZEOF(bufferbuffer));
	INIT:=FALSE;
END_IF
// -----------------------------------------------------------------------------
// MAIN LOGIC
// -----------------------------------------------------------------------------

// 1. Enable and run the TCP server.
myServer();

// Get the server's interface pointer.
itfServer := myServer.itfServer;
xEnableConnection := xServerActive;
xServerActive := itfServer <> 0;

// 2. Control the TCP connection function block's enable input with a delay.

// If a reset is initiated (xResetConnection is TRUE)...
IF xResetConnection THEN
    // Start the delay timer. Its IN is TRUE as long as xResetConnection is TRUE.
    fbResetDelayTimer(IN := TRUE, PT := T#10MS, Q => xDelayDone);
    
    // If the delay timer is done (Q is TRUE), then disable the connection.
    IF xDelayDone THEN
        xEnableConnection := FALSE;
		fb_MsgPakParser.ResetParser();
		dbuffer.clear();
    END_IF
ELSE
    // If no reset is initiated, ensure the timer is reset.
    fbResetDelayTimer(IN := FALSE);
    xDelayDone := FALSE; // Ensure xDelayDone is false when timer is off
END_IF

// Call the connection block with the controlled enable input.
myConnection(itfServer:=itfServer, xEnable:=xEnableConnection);

// 3. Main communication logic for an active connection.
IF myConnection.xActive THEN
    
    // --- Read from the client ---
    eReadErrorID := myConnection.Read(
        dbuffer.get_head(), // Address of the first byte in the array
        dbuffer.get_space(), // Total size of the buffer in bytes
        udiCount => udiBytesReceived // Number of bytes actually received
    );
	dbuffer.write_length(udiBytesReceived);

	(*eWriteErrorID := myConnection.Write(
            ADR(sSendBuffer[0]), // Address of the first byte to send
            udiBytesToSend // Number of bytes to send
        );*)


    // Check if data was successfully read without error and if any bytes were received.
    IF eReadErrorID = 0 AND dbuffer.size() > 0 THEN
        
			
		curPacketStartIdx:=0;
		curPacketLen:=0;
		TMP_buffer_ptr:=dbuffer.get_buffer();
		TMP_buff_size:=dbuffer.size();
		FOR i := dbuffer.size()-UDINT_TO_DINT(udiBytesReceived) TO dbuffer.size() - 1 DO
			fb_MsgPakParser(in_byte := TMP_buffer_ptr[i]);//feed in latest data
			IF fb_MsgPakParser.e_error = E_MsgPakLevelParser_ERROR.NONE THEN
					
				IF fb_MsgPakParser.ui_level_stack_size = 0 THEN
					//a packet 
					curPacketLen:=i-curPacketStartIdx+1;
					
					IF(curPacketLen<2) THEN//too short, might be an parse error
						//E_MsgPakLevelParser_ERROR.UNEXPECTED_BYTE;
						xResetConnection:=TRUE;
						EXIT;
					ELSE
						fb_MsgUnPak.Init(ADR(TMP_buffer_ptr[curPacketStartIdx]),curPacketLen);
						
						data_type:=fb_MsgUnPak.TryReadString('type','');
						
						IF(data_type='M')THEN//motion type
							WHILE GVL.minfo_buf_ridx.space() = 0 DO//dead wait
								// The timer must be called repeatedly inside the loop to make it run.
								fbWaitTimeout(IN:= TRUE, PT:= UDINT_TO_TIME(1));
							
								// If the timer's output (Q) becomes TRUE, exit the loop.
								IF fbWaitTimeout.Q THEN
									EXIT; // Exit the WHILE loop immediately
								END_IF;
							END_WHILE
							
							
							
							// space() returns UDINT — was >=0 (always true). Need free slot before writing.
							IF(GVL.minfo_buf_ridx.space() > 0)THEN
								tmp_ptr:=ADR(GVL.minfo_buf[GVL.minfo_buf_ridx.getHead()][0]);
								tmp_ptr[0]:=DINT_TO_BYTE(curPacketLen);//keep data len
								//MEM.MemMove(ADR(TMP_buffer_ptr[curPacketStartIdx]),ADR(tmp_ptr[1]),DINT_TO_UINT(curPacketLen));
								MEMUtils.MemCpy(ADR(tmp_ptr[1]),ADR(TMP_buffer_ptr[curPacketStartIdx]),DINT_TO_UINT(curPacketLen));
								GVL.minfo_buf_ridx.pushHead();
							END_IF
							
							
							
						END_IF
						
	        			udiBytesToSend := curPacketLen;
						
						MEMUtils.MemCpy(ADR(sSendBuffer[0]), ADR(TMP_buffer_ptr[curPacketStartIdx]), curPacketLen);
				        eWriteErrorID := myConnection.Write(
				            ADR(sSendBuffer[0]), // Address of the first byte to send
				            udiBytesToSend // Number of bytes to send
				        );
						curPacketStartIdx:=i+1;
						
					END_IF
					
				END_IF
			ELSE
				xResetConnection:=TRUE;
				EXIT;
				
			END_IF
		END_FOR
		IF(curPacketStartIdx<>0) THEN
			dbuffer.move_left(curPacketStartIdx);
		END_IF
		
		//dbuffer.clear();
    END_IF


END_IF // END IF myConnection.xActive

// 4. Connection tear-down and reset initiation logic.
// This is where a disconnect or error is detected, which then starts the delay.

// A) Detect a graceful disconnect (falling edge of xActive).
IF xLastConnectionActive AND NOT myConnection.xActive THEN
    xResetConnection := TRUE; // Initiate the reset process
END_IF

// B) Detect an abrupt disconnect (error).
IF myConnection.xError THEN
    xResetConnection := TRUE; // Initiate the reset process
END_IF

// C) Complete the reset process.
// Clear the xResetConnection flag ONLY when the delay is over (xDelayDone TRUE)
// AND the connection has actually become inactive (myConnection.xActive FALSE).
IF xResetConnection AND xDelayDone AND NOT myConnection.xActive THEN
    xResetConnection := FALSE; // Reset process is complete
END_IF

// 5. Update the state of the connection for the next PLC cycle.
xLastConnectionActive := myConnection.xActive;


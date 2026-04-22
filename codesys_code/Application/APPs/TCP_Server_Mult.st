PROGRAM TCP_Server_Mult
VAR CONSTANT
    // --- Define the maximum size of the incoming and outgoing strings ---
    mySize : UDINT := 1024;
    // --- Maximum number of simultaneous connections the server will handle ---
    MAX_CONNECTIONS : UINT := 5; // Example: Allow up to 5 concurrent clients
END_VAR

VAR
    // --- Server Function Block ---
    myIPAddress : NBS.IPv4Address := (ipAddress:='192.168.3.6');
    myServer : NBS.TCP_Server := (
        itfIPAddress:=myIPAddress,
        uiPort:=8123,
		xEnable:=TRUE,
		udiMaxConnections:=MAX_CONNECTIONS // Allow multiple connections
    );
    itfServer : NBS.IServer;
    xServerActive : BOOL;

    // --- Array of Connection Function Blocks ---
    myConnections : ARRAY [1..MAX_CONNECTIONS] OF NBS.TCP_Connection;
    
    // --- Data Buffers and Error IDs (Arrays for each connection) ---
    udiBytesReceived : ARRAY [1..MAX_CONNECTIONS] OF UDINT;
    eReadErrorID : ARRAY [1..MAX_CONNECTIONS] OF NBS.ERROR;
    eWriteErrorID : ARRAY [1..MAX_CONNECTIONS] OF NBS.ERROR;
    sReceiveBuffer : ARRAY [1..MAX_CONNECTIONS] OF STRING(mySize);
    sSendBuffer : ARRAY [1..MAX_CONNECTIONS] OF STRING(mySize + 10); // Prefix + data

    // --- State Control for Connection Management (Arrays for each connection) ---
    xResetConnection : ARRAY [1..MAX_CONNECTIONS] OF BOOL;      // Flag to initiate a reset process for each connection
    xLastConnectionActive : ARRAY [1..MAX_CONNECTIONS] OF BOOL; // Holds the state of xActive from previous cycle for each connection

    // --- Delay Timers for Connection Reset (Arrays for each connection) ---
    fbResetDelayTimer : ARRAY [1..MAX_CONNECTIONS] OF Standard.TON;
    
    // --- Loop Counter ---
    i : UINT; // Loop variable for iterating through connections
END_VAR
(* =========== IMPLEMENTATION =========== *)

// -----------------------------------------------------------------------------
// MAIN LOGIC
// -----------------------------------------------------------------------------

// 1. Enable and run the TCP server.
myServer();

// Get the server's interface pointer.
itfServer := myServer.itfServer;
xServerActive := itfServer <> 0;

// 2. Loop through each possible connection slot.
FOR i := 1 TO MAX_CONNECTIONS DO

    // --- Control the TCP connection function block's enable input with a delay ---
    // The connection should be enabled by default (when server is active).
    // It's disabled only after the reset delay completes.
    myConnections[i](itfServer:=itfServer, xEnable:=xServerActive AND NOT fbResetDelayTimer[i].Q);

    // 3. Main communication logic for an active connection.
    IF myConnections[i].xActive THEN
        
        // --- Read from the client ---
        eReadErrorID[i] := myConnections[i].Read(
            ADR(sReceiveBuffer[i]),
            SIZEOF(sReceiveBuffer[i]),
            udiCount => udiBytesReceived[i]
        );

        // Check if data was successfully read without error.
        IF eReadErrorID[i] = 0 AND udiBytesReceived[i] > 0 THEN
            
            // --- Process the received data ---
            sReceiveBuffer[i][udiBytesReceived[i]] := 0; // Null-terminate the string
            sSendBuffer[i] :=CONCAT(UINT_TO_STRING(i), CONCAT('echo:', sReceiveBuffer[i])); // Add prefix
            
            // --- Send the echoed data back to the client ---
            eWriteErrorID[i] := myConnections[i].Write(
                ADR(sSendBuffer[i]),
                LEN(sSendBuffer[i])
            );
            sReceiveBuffer[i] := ''; // Clear receive buffer

        END_IF
    END_IF

    // 4. Connection tear-down and reset initiation logic (per connection).
    // This is where a disconnect or error is detected, which then starts the delay for THIS connection.

    // A) Detect a graceful disconnect (falling edge of xActive).
    IF xLastConnectionActive[i] AND NOT myConnections[i].xActive THEN
        xResetConnection[i] := TRUE; // Initiate the reset process for this connection
    END_IF

    // B) Detect an abrupt disconnect (error).
    IF myConnections[i].xError THEN
        xResetConnection[i] := TRUE; // Initiate the reset process for this connection
    END_IF

    // C) Timer Logic for the 100ms delay before actual reset.
    // The timer starts when xResetConnection[i] becomes TRUE.
    fbResetDelayTimer[i](IN := xResetConnection[i], PT := T#100MS);
    
    // D) Complete the reset process.
    // Clear the xResetConnection[i] flag ONLY when the delay is over (fbResetDelayTimer[i].Q is TRUE)
    // AND the connection has actually become inactive (myConnections[i].xActive is FALSE).
    IF xResetConnection[i] AND fbResetDelayTimer[i].Q AND NOT myConnections[i].xActive THEN
        xResetConnection[i] := FALSE; // Reset process for this connection is complete
    END_IF

    // 5. Update the state of this connection for the next PLC cycle.
    xLastConnectionActive[i] := myConnections[i].xActive;

END_FOR // End of FOR loop
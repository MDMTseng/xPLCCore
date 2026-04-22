PROGRAM WebServer_SIMPLE
VAR CONSTANT
    mySize : UDINT := 1255;
END_VAR

VAR CONSTANT
    // --- Define your simple HTML page as a constant ---
    sHtmlBody : STRING(1024) :=
        '<!DOCTYPE html><html><head><title>PLC Status</title></head><body><h1>Hello from my CoDeSys PLC!</h1><p>This page is served directly from the controller.</p></body></html>';

    // --- Define the HTTP header template ---
    // We REMOVE the 'Connection: close' header to keep the connection alive.
    // We use a placeholder 'LL' which we will replace with the actual length.
    sHttpHeaderTemplate : STRING(1024) := 
        'HTTP/1.1 200 OK$r$nContent-Type: text/html$r$nContent-Length: LL$r$n$r$n'; // Removed 'Connection: close'
END_VAR

VAR
    /// The certificate of the client should be located in the store under the trusted folder!!

    myIPAddress : NBS.IPv4Address := (ipAddress:='192.168.3.6');
    sMyCommonName : STRING := 'MyPLCserverName';

    myConnection : NBS.TCP_Connection;
    xConnectionReset : BOOL; // Flag to control the connection reset

    myServer : NBS.TCP_Server := (
        itfIPAddress:=myIPAddress,
        uiPort:=8123,
		xEnable:=TRUE,
		udiMaxConnections:=1
    );

    itfServer : NBS.IServer;
    xActive : BOOL;
    
    udiServerCount : UDINT;
    eServerTxErrorID, eServerRxErrorID : NBS.ERROR;
    myServerData : STRING(mySize);
	
	sFullHttpResponse : STRING(2024); // Make sure this is large enough
    sContentLength    : STRING(4);    // To hold the string version of the length
END_VAR
(* =========== IMPLEMENTATION =========== *)

// Set myServer.xEnable to start
myServer();

itfServer := myServer.itfServer;
xActive := itfServer <> 0;

// Call the connection block. We control its enable input using a reset flag.
myConnection(itfServer:=itfServer, xEnable:=xActive AND NOT xConnectionReset);

IF myConnection.xActive THEN
    // The connection is active. Read data from the client.
    eServerRxErrorID := myConnection.Read(ADR(myServerData), mySize, udiCount=>udiServerCount);
    IF eServerRxErrorID = 0 AND udiServerCount > 0 THEN
        // A new request was received.
		
		        // ---- 1. BUILD THE HTTP RESPONSE ----
        
        // Clear the response string
        sFullHttpResponse := '';
        
        // Calculate the length of the HTML body and convert it to a string
        sContentLength := DINT_TO_STRING(LEN(sHtmlBody));
        
        // Build the header by replacing the 'LL' placeholder with the actual length
        sFullHttpResponse := REPLACE(STR1:=sHttpHeaderTemplate, STR2:=sContentLength, L:=LEN(sContentLength), P:=FIND(sHttpHeaderTemplate, 'LL'));
        
        // Append the HTML body to the headers
        sFullHttpResponse := CONCAT(sFullHttpResponse, sHtmlBody);
        
        // ---- 2. SEND THE RESPONSE ----
        eServerTxErrorID := myConnection.Write(
                                ADR(sFullHttpResponse), 
                                LEN(sFullHttpResponse)
                            );
                            
        // ---- 3. DO NOT CLOSE THE CONNECTION ----
        // The server now waits for the next request on the same connection.
	END_IF
END_IF

// This is the crucial part for tearing down the connection:
// How the PLC detects a client disconnect and resets the connection stack.

// 1. Detect if the connection has an error.
//    This happens on an abrupt disconnect (e.g., pulling the network cable).
IF myConnection.xError THEN
    // An error occurred. Set the reset flag to tear down the connection.
    xConnectionReset := TRUE;
END_IF

// 2. After setting the reset flag, the connection function block needs to be
//    disabled for at least one PLC cycle to reset.
//    Once it's no longer active, we can clear the flag.
IF xConnectionReset AND NOT myConnection.xActive THEN
    // The connection has been torn down. We are ready to accept a new one.
    xConnectionReset := FALSE;
END_IF
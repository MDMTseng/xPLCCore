
FUNCTION_BLOCK FB_TcpMsgPakServer
VAR_INPUT
    // --- Configuration Inputs ---
    IP_ADDRESS              : NBS.IIPAddress;  // IP Address for the server to listen on.
    PORT                    : UINT;             // Port number for the server to listen on.
    ENABLE                  : BOOL;             // Master switch to enable/disable the server.

    // --- Control Input ---
    PKT_ALLOW_NEXT          : BOOL;             // Set to TRUE to acknowledge processing of the current packet and allow the FB to provide the next one.
    // External force-reset request: caller (TCP_MSGPAK_Server) raises this
    // for one cycle when it observes sustained TX stall (xActive=TRUE but
    // Send keeps failing past TX_STALL_DROP_THRESHOLD head-drops). The
    // RX-side idle watchdog only fires on RX silence, so a half-open TX
    // path with ongoing client RX would otherwise never trigger a reset.
    xForceReset             : BOOL;
END_VAR

VAR_OUTPUT
    // --- Status and Data Outputs ---
    CLIENT                  : NBS.TCP_Connection; // The active TCP connection object. Provides access to status like xActive, xError, etc.
    PKT_IDX                 : UDINT;              // Increments each time a complete data packet is found. BUFFER_PTR and BUFFER_LEN are valid.
    BUFFER_PTR              : POINTER TO BYTE;    // Pointer to the start of the available data packet. 0 (NULL) if no data is available.
    BUFFER_LEN              : UDINT;              // Length of the available data packet in bytes. 0 if no data is available.
END_VAR

VAR CONSTANT
    MAX_BUFFER_SIZE         : UDINT := 2048;
    IDLE_TIMEOUT            : TIME  := T#7S;   // Reset connection if no bytes received for this duration (handles cable unplug / silent crash). Sized > AxisGroupSM A3 supervisor (5s) so the FSM trips Error first; just after, this reclaims the socket so a new host can reconnect without waiting for a TCP keepalive.
END_VAR

VAR
    // --- Server and Connection Function Blocks ---
    fbServer                : NBS.TCP_Server;
    itfServer               : NBS.IServer;
    xServerActive           : BOOL;
    xEnableConnection       : BOOL;               // Controls the xEnable input of the TCP_Connection FB.

    // --- Data Buffers and Parsing ---
    udiBytesReceived        : UDINT;
    eReadErrorID            : NBS.ERROR;
    aInternalBuffer         : ARRAY [0..MAX_BUFFER_SIZE-1] OF BYTE;
    fbDataBuffer            : FB_DataBuffer;
    fbMsgPakParser          : FB_MsgPakLevelParser;

    // --- Connection Reset State ---
    xResetConnection        : BOOL := FALSE;      // Flag to initiate a connection reset process.
    xLastConnectionActive   : BOOL := FALSE;      // Holds CLIENT.xActive from the previous cycle.
    fbResetDelayTimer       : Standard.TON;       // Grace-period delay before tearing down the connection.
    fbIdleWatchdog          : Standard.TON;       // Fires if no bytes are received within IDLE_TIMEOUT (silent disconnect detection).

    iProcessedPacketLen     : DINT;               // Byte length of the packet currently exposed on outputs.
    iParseIdx               : DINT;               // Index of the next unparsed byte in the data buffer.

    // --- Internal Helpers ---
    tmpParserPtr            : POINTER TO BYTE;
    pre_stack_size          : DINT;
    bFirstCycle             : BOOL := TRUE;

    // --- Debug Counters ---
    DBG_recvCount           : DINT := 0;
    DBG_pktPopCount         : DINT := 0;
END_VAR
(* =========== IMPLEMENTATION =========== *)


//==================================================================================================
// FB IMPLEMENTATION
//==================================================================================================

// --- 1. First Cycle Initialization ---
IF bFirstCycle THEN
    fbMsgPakParser.ResetParser();
    iParseIdx := 0;
    fbDataBuffer.ResetBuffer(ADR(aInternalBuffer[0]), SIZEOF(aInternalBuffer));
    bFirstCycle := FALSE;
END_IF

// --- 2. Run the TCP server ---
fbServer(
    itfIPAddress      := IP_ADDRESS,
    uiPort            := PORT,
    xEnable           := ENABLE,
    udiMaxConnections := 1
);
itfServer     := fbServer.itfServer;
xServerActive := itfServer <> 0;

// --- 3. Detect connection loss or error (triggers reset) ---
IF (xLastConnectionActive AND NOT CLIENT.xActive) OR CLIENT.xError THEN
    xResetConnection := TRUE;
END_IF

// External force-reset (TX-side half-open detection lives in the caller;
// it raises xForceReset once it has accumulated enough head-drops to
// declare the socket dead). Counted separately from idle/read/parser
// resets via GVL.TxStallResetCount.
IF xForceReset AND NOT xResetConnection THEN
    xResetConnection := TRUE;
    GVL.TxStallResetCount := GVL.TxStallResetCount + 1;
END_IF

// Idle watchdog -- detects silent disconnections (cable unplug, client crash
// without RST, frozen renderer). The timer is fed whenever bytes arrive.
// If IDLE_TIMEOUT elapses with the socket still reporting xActive=TRUE,
// force a reset so the single-connection slot is released for the next host.
// AxisGroupSM's A3 heartbeat supervisor trips Error before this fires (5s
// vs 7s) so the FSM is already safely halted by the time the socket churns.
fbIdleWatchdog(IN := CLIENT.xActive AND NOT xResetConnection, PT := IDLE_TIMEOUT);
IF fbIdleWatchdog.Q AND NOT xResetConnection THEN
    xResetConnection := TRUE;
    GVL.IdleResetCount := GVL.IdleResetCount + 1;
END_IF

// --- 4. Connection reset state machine ---
// Phase 1 (delay):  20ms grace period — keep connection alive, clear stale outputs.
// Phase 2 (flush):  Disable CLIENT, flush buffers, wait for TCP stack to confirm closure.
// Phase 3 (done):   xActive dropped — clear flag, next cycle re-enables the connection.
IF xResetConnection THEN
    fbResetDelayTimer(IN := TRUE, PT := T#20MS);
    // Clear packet outputs and parser state immediately so no stale data is exposed.
    PKT_IDX    := 0;
    BUFFER_PTR := 0;
    BUFFER_LEN := 0;
    iParseIdx  := 0;
    DBG_recvCount   := 0;
    DBG_pktPopCount := 0;
    IF fbResetDelayTimer.Q THEN
        // Grace period elapsed — disable CLIENT and flush internal buffers.
        xEnableConnection := FALSE;
        fbMsgPakParser.ResetParser();
        fbDataBuffer.Clear();
        // TCP stack has confirmed the socket is closed; ready to accept a new connection.
        IF NOT CLIENT.xActive THEN
            xResetConnection := FALSE;
        END_IF
    ELSE
        xEnableConnection := xServerActive; // Still within grace period.
    END_IF
ELSE
    fbResetDelayTimer(IN := FALSE);
    xEnableConnection := xServerActive;
END_IF

// --- 5. Run the TCP connection ---
CLIENT(itfServer := itfServer, xEnable := xEnableConnection);

// --- 6. Consume the previously exposed packet from the buffer ---
IF iProcessedPacketLen > 0 THEN
    fbDataBuffer.MoveLeft(iProcessedPacketLen);
    iParseIdx           := iParseIdx - iProcessedPacketLen;
    iProcessedPacketLen := 0;
END_IF

// --- 7. Read incoming data and parse for complete MessagePack packets ---
IF PKT_ALLOW_NEXT THEN
    // Read new data into the buffer if the connection is active and space is available.
    IF CLIENT.xActive AND fbDataBuffer.GetSpace() > 0 THEN
        eReadErrorID := CLIENT.Read(
            fbDataBuffer.GetHead(),
            fbDataBuffer.GetSpace(),
            udiCount => udiBytesReceived
        );
        IF eReadErrorID <> NBS.ERROR.NO_ERROR THEN
            IF NOT xResetConnection THEN
                GVL.ReadErrorResetCount := GVL.ReadErrorResetCount + 1;
            END_IF;
            xResetConnection := TRUE;
        ELSIF udiBytesReceived > 0 THEN
            fbIdleWatchdog(IN := FALSE);  // Feed the watchdog — connection is alive.
            DBG_recvCount := DBG_recvCount + udiBytesReceived;
            fbDataBuffer.WriteLength(udiBytesReceived);
        END_IF
    END_IF

    // Parse buffered bytes until a complete packet is found.
    // 2026-06-23 code-review fix: was `< Size() - 1` which made a
    // single trailing byte sit in the buffer for one extra scan
    // (FOR bound is `TO Size()-1` so Size()=1 -> FOR 0 TO 0 still runs;
    // the IF guard prevented entering the FOR at all). `< Size()` now.
    IF iParseIdx < fbDataBuffer.Size() THEN
        tmpParserPtr := fbDataBuffer.GetBuffer();
        FOR iParseIdx := iParseIdx TO fbDataBuffer.Size() - 1 DO
            pre_stack_size := fbMsgPakParser.ui_level_stack_size;
            fbMsgPakParser(in_byte := tmpParserPtr[iParseIdx]);

            IF fbMsgPakParser.e_error <> E_MsgPakLevelParser_ERROR.NONE THEN
                IF NOT xResetConnection THEN
                    GVL.ParserErrorResetCount := GVL.ParserErrorResetCount + 1;
                END_IF;
                xResetConnection := TRUE;
                EXIT;
            END_IF

            // Parser returned to root level — a complete packet has been assembled.
            IF pre_stack_size <> 0 AND fbMsgPakParser.ui_level_stack_size = 0 THEN
                iProcessedPacketLen := iParseIdx + 1;
                IF iProcessedPacketLen < 2 THEN
                    xResetConnection := TRUE;
                    EXIT;
                END_IF
                BUFFER_PTR := tmpParserPtr;
                BUFFER_LEN := DINT_TO_UDINT(iProcessedPacketLen);
                PKT_IDX    := PKT_IDX + 1;
                iParseIdx  := iParseIdx + 1;
                EXIT;
            END_IF
        END_FOR
    END_IF
END_IF

// --- 8. Update previous-cycle connection state ---
xLastConnectionActive := CLIENT.xActive;

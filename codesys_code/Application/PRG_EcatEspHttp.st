PROGRAM PRG_EcatEspHttp
VAR CONSTANT
    HTML_LEN        : UDINT := 3061;
END_VAR
VAR
    sHtml           : STRING(3062) := '<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>EasyCAT &#32232;&#30908;&#22120;</title><style>body{font-family:system-ui,sans-serif;background:#f4f5f7;color:#1f2328;margin:0;padding:16px}h1{font-size:18px;margin:0 0 12px}.row{display:flex;flex-wrap:wrap;gap:12px}.card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:150px}.k{font-size:12px;color:#656d76}.v{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}.ok{color:#1a7f37}.bad{color:#cf222e}.warn{color:#9a6700}svg{display:block}#conn{font-size:12px;color:#656d76;margin-top:12px}</style></head><body><h1>EasyCAT + ESP32 &#32232;&#30908;&#22120;&#29376;&#24907;</h1><div class="row"><div class="card"><svg width="170" height="170" viewBox="-85 -85 170 170"><circle r="78" fill="#f4f5f7" stroke="#d0d7de" stroke-width="2"/><line id="nd" x1="0" y1="0" x2="0" y2="-68" stroke="#0969da" stroke-width="5" stroke-linecap="round"/><circle r="5" fill="#0969da"/></svg></div><div class="card"><div class="k">&#35282;&#24230;</div><div class="v" id="deg">-</div><div class="k" style="margin-top:10px">&#21407;&#22987;&#20540; (0-4095)</div><div class="v" id="raw">-</div></div><div class="card"><div class="k">&#22810;&#22280;&#20301;&#32622; (counts)</div><div class="v" id="pos">-</div><div class="k" style="margin-top:10px">&#22280;&#25976;</div><div class="v" id="turns">-</div></div></div><div class="row" style="margin-top:12px"><div class="card"><div class="k">ESP32</div><div class="v" id="alive">-</div></div><div class="card"><div class="k">EtherCAT</div><div class="v" id="ecat">-</div></div><div class="card"><div class="k">&#30913;&#37941;</div><div class="v" id="mag">-</div></div><div class="card"><div class="k">AGC / I2C &#37679;&#35492;</div><div class="v" id="agc">-</div></div></div><div id="conn">-</div><script>var S={1:"INIT",2:"PREOP",3:"BOOT",4:"SAFEOP",8:"OP"};function g(i){return document.getElementById(i)}function put(i,h,c){var e=g(i);e.innerHTML=h;e.className="v "+(c||"")}function tick(){fetch("/s",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){var deg=j.raw*360/4096;g("nd").setAttribute("transform","rotate("+deg.toFixed(1)+")");put("deg",deg.toFixed(1)+"&deg;");put("raw",j.raw);put("pos",j.pos);put("turns",(j.pos/4096).toFixed(3));put("alive",j.alive?"&#36939;&#20316;&#20013;":"&#28961;&#24515;&#36339;",j.alive?"ok":"bad");put("ecat",S[j.ecat]||j.ecat,j.ecat==8?"ok":"bad");var m=j.st&1?["&#28961;&#24863;&#28204;&#22120;","bad"]:j.st&16?["&#22826;&#24369;","warn"]:j.st&8?["&#22826;&#24375;","warn"]:j.st&32?["&#27491;&#24120;","ok"]:["&#26410;&#20597;&#28204;","bad"];put("mag",m[0],m[1]);put("agc",j.agc+" / "+j.err);g("conn").innerHTML="&#26356;&#26032; "+new Date().toLocaleTimeString()+" &middot; &#35531;&#27714; "+j.hb;setTimeout(tick,200)}).catch(function(){g("conn").innerHTML="&#36899;&#32218;&#20013;&#26039;&#65292;&#37325;&#35430;&#20013;";setTimeout(tick,1000)})}tick();</script></body></html>';
    sHdrHtml        : STRING(200) := 'HTTP/1.1 200 OK$R$NContent-Type: text/html; charset=utf-8$R$NCache-Control: no-store$R$NConnection: close$R$N$R$N';
    sHdrJson        : STRING(200) := 'HTTP/1.1 200 OK$R$NContent-Type: application/json$R$NCache-Control: no-store$R$NConnection: close$R$N$R$N';
    sHdrText        : STRING(200) := 'HTTP/1.1 200 OK$R$NContent-Type: text/plain$R$NCache-Control: no-store$R$NConnection: close$R$N$R$N';
    sHdr404         : STRING(120) := 'HTTP/1.1 404 Not Found$R$NContent-Length: 0$R$NConnection: close$R$N$R$N';
    sJson           : STRING(255);

    ipAny           : NBS.IPv4Address := (ipAddress:='0.0.0.0');
    fbServer        : NBS.TCP_Server := (itfIPAddress:=ipAny, uiPort:=8126, xEnable:=TRUE, udiMaxConnections:=1);
    fbConn          : NBS.TCP_Connection;
    itfServer       : NBS.IServer;

    abyReq          : ARRAY[0..511] OF BYTE;
    udiReqCount     : UDINT;
    eRx             : NBS.ERROR;
    eTx             : NBS.ERROR;
    xReset          : BOOL;
    xResponded      : BOOL;
    uiCloseDelay    : UINT;
    udiRequests     : UDINT;
    // GET /e?s=<seq>: event log (GVL.EvHead) from <seq>, text lines
    // "seq t_ms kind val", first line "head <EvHead>". At most EV_MAX
    // lines per request; the client asks again from the last seq + 1.
    abyEv           : ARRAY[0..6143] OF BYTE;
    udiEvLen        : UDINT;
    udiEvFrom       : UDINT;
    udiEvTo         : UDINT;
    udiEvHead       : UDINT;
    udiEvSeq        : UDINT;
    udiEvSlot       : UDINT;
    iEvPos          : INT;
    sEvLine         : STRING(60);
    EV_MAX          : UDINT := 150;
    // One connection at a time: a client that connects and never sends a
    // request (killed mid-request) held it forever and every later request
    // was refused (seen 2026-09-24). Drop it after 1 s.
    tonIdle         : TON;
    udiIdleDrops    : UDINT;
    // Also seen: after a client vanished the connection FB sat enabled but
    // neither busy nor active -- no error, never listening again. Re-arm
    // it after a few such scans (debounced: xBusy may lag the enable).
    uiDeadScans     : UINT;
    udiRearms       : UDINT;

    // ---- Stream on :8128: one persistent connection, the PLC pushes lines
    // (no request, no reconnect per sample -- the HTTP polling above costs
    // a connect/close per request and stalls when a SYN is dropped).
    //   e <seq> <t_ms> <kind> <val>      every GVL.EvHead event, from connect time
    //   v <sd> <bt> <ff> <tp> <ad> <rp> <bx> <by>   PRG_SimIo counters, every 20 ms
    // Used by tools/sim/vision_mock.py.
    fbStreamSrv     : NBS.TCP_Server := (itfIPAddress:=ipAny, uiPort:=8128, xEnable:=TRUE, udiMaxConnections:=1);
    fbStream        : NBS.TCP_Connection;
    xStreamReset    : BOOL;
    xStreamOn       : BOOL;
    uiStreamDead    : UINT;
    udiStreamSeq    : UDINT;
    udiStreamHead   : UDINT;
    udiStreamSlot   : UDINT;
    udiStreamLen    : UDINT;
    iStreamLines    : INT;
    lStreamLastV    : LINT;
    sStreamLine     : STRING(120);
    abyStream       : ARRAY[0..4095] OF BYTE;
    eStreamTx       : NBS.ERROR;
    udiStreamClients : UDINT;
    udiStreamLost   : UDINT;
    udiStreamWriteErr : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
fbServer();
itfServer := fbServer.itfServer;
fbConn(itfServer := itfServer, xEnable := (itfServer <> 0) AND NOT xReset);
// A reset is complete once the FB has been called disabled and is idle;
// the next scan enables it again (a clean enable edge).
IF xReset AND NOT fbConn.xActive AND NOT fbConn.xBusy THEN
    xReset := FALSE;
    xResponded := FALSE;
    uiDeadScans := 0;
END_IF
IF (itfServer <> 0) AND NOT xReset AND NOT fbConn.xBusy AND NOT fbConn.xActive THEN
    uiDeadScans := uiDeadScans + 1;
    IF uiDeadScans > 10 THEN
        udiRearms := udiRearms + 1;
        xReset := TRUE;
    END_IF
ELSE
    uiDeadScans := 0;
END_IF

IF fbConn.xActive AND NOT xResponded THEN
    eRx := fbConn.Read(ADR(abyReq), SIZEOF(abyReq), udiCount => udiReqCount);
    IF eRx = NBS.ERROR.NO_ERROR AND udiReqCount >= 6 THEN
        udiRequests := udiRequests + 1;
        // Request line: "GET /s" -> status JSON, "GET /v" -> sim JSON,
        // "GET / " -> page.
        IF abyReq[5] = 16#73 THEN
            sJson := '{"alive":';
            IF PRG_EcatEsp.xEspAlive THEN sJson := CONCAT(sJson, '1'); ELSE sJson := CONCAT(sJson, '0'); END_IF
            sJson := CONCAT(sJson, ',"ecat":');
            sJson := CONCAT(sJson, UINT_TO_STRING(TO_UINT(EasyCAT.wState)));
            sJson := CONCAT(sJson, ',"raw":');
            sJson := CONCAT(sJson, UINT_TO_STRING(PRG_EcatEsp.uiEncRaw));
            sJson := CONCAT(sJson, ',"pos":');
            sJson := CONCAT(sJson, DINT_TO_STRING(PRG_EcatEsp.diEncPos));
            sJson := CONCAT(sJson, ',"st":');
            sJson := CONCAT(sJson, USINT_TO_STRING(PRG_EcatEsp.byEncStatus));
            sJson := CONCAT(sJson, ',"err":');
            sJson := CONCAT(sJson, USINT_TO_STRING(PRG_EcatEsp.byEncI2cErr));
            sJson := CONCAT(sJson, ',"agc":');
            sJson := CONCAT(sJson, USINT_TO_STRING(PRG_EcatEsp.byEncAgc));
            sJson := CONCAT(sJson, ',"hb":');
            sJson := CONCAT(sJson, UDINT_TO_STRING(udiRequests));
            sJson := CONCAT(sJson, '}');
            eTx := fbConn.Write(ADR(sHdrJson), INT_TO_UDINT(LEN(sHdrJson)));
            eTx := fbConn.Write(ADR(sJson), INT_TO_UDINT(LEN(sJson)));
        ELSIF abyReq[5] = 16#76 THEN
            sJson := '{"sd":';
            sJson := CONCAT(sJson, UDINT_TO_STRING(PRG_SimIo.udiSide));
            sJson := CONCAT(sJson, ',"bt":');
            sJson := CONCAT(sJson, UDINT_TO_STRING(PRG_SimIo.udiBtm));
            sJson := CONCAT(sJson, ',"ff":');
            sJson := CONCAT(sJson, UDINT_TO_STRING(PRG_SimIo.udiFeeder));
            sJson := CONCAT(sJson, ',"tp":');
            sJson := CONCAT(sJson, UDINT_TO_STRING(PRG_SimIo.udiTop));
            sJson := CONCAT(sJson, ',"ad":');
            sJson := CONCAT(sJson, UDINT_TO_STRING(PRG_SimIo.udiReelAdv));
            sJson := CONCAT(sJson, ',"rp":');
            sJson := CONCAT(sJson, LREAL_TO_STRING(reelpullmotor.fActPosition));
            sJson := CONCAT(sJson, ',"bx":');
            sJson := CONCAT(sJson, REAL_TO_STRING(PRG_SimIo.rBtmX));
            sJson := CONCAT(sJson, ',"by":');
            sJson := CONCAT(sJson, REAL_TO_STRING(PRG_SimIo.rBtmY));
            sJson := CONCAT(sJson, '}');
            eTx := fbConn.Write(ADR(sHdrJson), INT_TO_UDINT(LEN(sHdrJson)));
            eTx := fbConn.Write(ADR(sJson), INT_TO_UDINT(LEN(sJson)));
        ELSIF abyReq[5] = 16#65 THEN
            // "GET /e?s=123 ..." -- parse the digits after "?s=".
            udiEvFrom := 0;
            IF abyReq[6] = 16#3F AND abyReq[7] = 16#73 AND abyReq[8] = 16#3D THEN
                iEvPos := 9;
                WHILE (iEvPos < 40) AND (abyReq[iEvPos] >= 16#30) AND (abyReq[iEvPos] <= 16#39) DO
                    udiEvFrom := udiEvFrom * 10 + BYTE_TO_UDINT(abyReq[iEvPos] - 16#30);
                    iEvPos := iEvPos + 1;
                END_WHILE
            END_IF
            udiEvHead := GVL.EvHead;
            // Keep clear of slots the writer may be reusing.
            IF (udiEvHead > 1000) AND (udiEvFrom < udiEvHead - 1000) THEN
                udiEvFrom := udiEvHead - 1000;
            END_IF
            udiEvTo := udiEvHead;
            IF (udiEvTo > udiEvFrom + EV_MAX) THEN udiEvTo := udiEvFrom + EV_MAX; END_IF
            sEvLine := CONCAT('head ', UDINT_TO_STRING(udiEvHead));
            sEvLine := CONCAT(sEvLine, '$N');
            udiEvLen := INT_TO_UDINT(LEN(sEvLine));
            MEMUtils.MemCpy(ADR(abyEv[0]), ADR(sEvLine), INT_TO_UINT(LEN(sEvLine)));
            udiEvSeq := udiEvFrom;
            WHILE udiEvSeq < udiEvTo DO
                udiEvSlot := udiEvSeq MOD 1024;
                sEvLine := CONCAT(UDINT_TO_STRING(udiEvSeq), ' ');
                sEvLine := CONCAT(sEvLine, UDINT_TO_STRING(GVL.EvT[udiEvSlot]));
                sEvLine := CONCAT(sEvLine, ' ');
                sEvLine := CONCAT(sEvLine, BYTE_TO_STRING(GVL.EvKind[udiEvSlot]));
                sEvLine := CONCAT(sEvLine, ' ');
                sEvLine := CONCAT(sEvLine, UDINT_TO_STRING(GVL.EvVal[udiEvSlot]));
                sEvLine := CONCAT(sEvLine, '$N');
                MEMUtils.MemCpy(ADR(abyEv[udiEvLen]), ADR(sEvLine), INT_TO_UINT(LEN(sEvLine)));
                udiEvLen := udiEvLen + INT_TO_UDINT(LEN(sEvLine));
                udiEvSeq := udiEvSeq + 1;
            END_WHILE
            eTx := fbConn.Write(ADR(sHdrText), INT_TO_UDINT(LEN(sHdrText)));
            eTx := fbConn.Write(ADR(abyEv), udiEvLen);
        ELSIF abyReq[5] = 16#20 THEN
            eTx := fbConn.Write(ADR(sHdrHtml), INT_TO_UDINT(LEN(sHdrHtml)));
            eTx := fbConn.Write(ADR(sHtml), HTML_LEN);
        ELSE
            eTx := fbConn.Write(ADR(sHdr404), INT_TO_UDINT(LEN(sHdr404)));
        END_IF
        xResponded := TRUE;
        uiCloseDelay := 0;
    END_IF
END_IF

tonIdle(IN := fbConn.xActive AND NOT xResponded, PT := T#1S);
IF tonIdle.Q THEN
    udiIdleDrops := udiIdleDrops + 1;
    xReset := TRUE;
END_IF

// Give the stack a few cycles to flush, then close ("Connection: close").
IF xResponded THEN
    uiCloseDelay := uiCloseDelay + 1;
    IF uiCloseDelay > 5 THEN
        xReset := TRUE;
    END_IF
END_IF
IF fbConn.xError THEN
    xReset := TRUE;
END_IF


// ---- Event / counter stream on :8128 -------------------------------------
fbStreamSrv();
fbStream(itfServer := fbStreamSrv.itfServer, xEnable := (fbStreamSrv.itfServer <> 0) AND NOT xStreamReset);
IF xStreamReset AND NOT fbStream.xActive AND NOT fbStream.xBusy THEN
    xStreamReset := FALSE;
    xStreamOn := FALSE;
    uiStreamDead := 0;
END_IF
// Same re-arm as the HTTP connection: enabled but neither busy nor active.
IF (fbStreamSrv.itfServer <> 0) AND NOT xStreamReset AND NOT fbStream.xBusy AND NOT fbStream.xActive THEN
    uiStreamDead := uiStreamDead + 1;
    IF uiStreamDead > 10 THEN
        xStreamReset := TRUE;
    END_IF
ELSE
    uiStreamDead := 0;
END_IF

IF fbStream.xActive AND NOT xStreamReset THEN
    IF NOT xStreamOn THEN
        xStreamOn := TRUE;
        udiStreamSeq := GVL.EvHead;          // from now on
        lStreamLastV := 0;
        udiStreamClients := udiStreamClients + 1;
    END_IF
    udiStreamLen := 0;
    udiStreamHead := GVL.EvHead;
    IF (udiStreamHead > 1000) AND (udiStreamSeq < udiStreamHead - 1000) THEN
        udiStreamLost := udiStreamLost + (udiStreamHead - 1000 - udiStreamSeq);
        udiStreamSeq := udiStreamHead - 1000;
    END_IF
    iStreamLines := 0;
    WHILE (udiStreamSeq < udiStreamHead) AND (iStreamLines < 60) DO
        udiStreamSlot := udiStreamSeq MOD 1024;
        sStreamLine := CONCAT('e ', UDINT_TO_STRING(udiStreamSeq));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(GVL.EvT[udiStreamSlot]));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, BYTE_TO_STRING(GVL.EvKind[udiStreamSlot]));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(GVL.EvVal[udiStreamSlot]));
        sStreamLine := CONCAT(sStreamLine, '$N');
        MEMUtils.MemCpy(ADR(abyStream[udiStreamLen]), ADR(sStreamLine), INT_TO_UINT(LEN(sStreamLine)));
        udiStreamLen := udiStreamLen + INT_TO_UDINT(LEN(sStreamLine));
        udiStreamSeq := udiStreamSeq + 1;
        iStreamLines := iStreamLines + 1;
    END_WHILE
    IF (AxisGroupSM.RuntimeMs - lStreamLastV) >= 20 THEN
        lStreamLastV := AxisGroupSM.RuntimeMs;
        sStreamLine := CONCAT('v ', UDINT_TO_STRING(PRG_SimIo.udiSide));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(PRG_SimIo.udiBtm));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(PRG_SimIo.udiFeeder));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(PRG_SimIo.udiTop));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, UDINT_TO_STRING(PRG_SimIo.udiReelAdv));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, LREAL_TO_STRING(reelpullmotor.fActPosition));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, REAL_TO_STRING(PRG_SimIo.rBtmX));
        sStreamLine := CONCAT(sStreamLine, ' ');
        sStreamLine := CONCAT(sStreamLine, REAL_TO_STRING(PRG_SimIo.rBtmY));
        sStreamLine := CONCAT(sStreamLine, '$N');
        MEMUtils.MemCpy(ADR(abyStream[udiStreamLen]), ADR(sStreamLine), INT_TO_UINT(LEN(sStreamLine)));
        udiStreamLen := udiStreamLen + INT_TO_UDINT(LEN(sStreamLine));
    END_IF
    IF udiStreamLen > 0 THEN
        eStreamTx := fbStream.Write(ADR(abyStream), udiStreamLen);
        IF eStreamTx <> NBS.ERROR.NO_ERROR THEN
            // client gone or send buffer full: drop it, it reconnects
            udiStreamWriteErr := udiStreamWriteErr + 1;
            xStreamReset := TRUE;
        END_IF
    END_IF
END_IF
IF fbStream.xError THEN
    xStreamReset := TRUE;
END_IF

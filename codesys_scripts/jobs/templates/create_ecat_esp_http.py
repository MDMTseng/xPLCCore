# -*- coding: utf-8 -*-
# Create PRG_EcatEspHttp: a tiny HTTP status page served by the PLC itself
# for the EasyCAT + ESP32 + encoder slave.
#
#   rpc.py exec --file jobs/templates/create_ecat_esp_http.py
#   browser:  http://192.168.1.70:8126/
#
# Separate port on purpose. The machine UI's msgpack server (8125) takes a
# single client and trips the FSM into Error after 5 s without a PING, so
# a status page must not touch it. This one needs neither the daemon nor
# anything running on the PC.
#
#   GET /    the page (HTML + JS, polls /s every 200 ms)
#   GET /s   JSON: {alive, ecat, raw, pos, st, err, agc, hb, rt}
#   GET /v   JSON for tools/sim/vision_mock.py (PRG_SimIo counters):
#            {sd, bt, ff, tp, ad, bx, by} -- camera trigger edges (side,
#            bottom, feeder, top), ReelAdv edges, arm X/Y at the last
#            bottom-camera edge
#   other    404
#
# One connection at a time, "Connection: close" after every response, the
# same NBS TCP_Server/TCP_Connection pattern as WebServer_SIMPLE. The page
# is generated here: non-ASCII text becomes HTML entities (&#NNNN;)
# because an IEC STRING is single-byte, and its length is computed here so
# the PLC never has to LEN() a string longer than 255 characters.
#
# Runs in the Comm task, next to TCP_MSGPAK_Server. Reads PRG_EcatEsp,
# which decodes the slave's bytes in EtherCAT_Task.
#
# Idempotent. Builds; downloading is a separate step.

from System import Guid

NAME = "PRG_EcatEspHttp"
TASK = "Comm"
PORT = 8126
BUILD = Guid("{97f48d64-a2a3-4856-b640-75c046e37ea9}")

PAGE = u"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EasyCAT 編碼器</title>
<style>
body{font-family:system-ui,sans-serif;background:#f4f5f7;color:#1f2328;margin:0;padding:16px}
h1{font-size:18px;margin:0 0 12px}
.row{display:flex;flex-wrap:wrap;gap:12px}
.card{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.08);min-width:150px}
.k{font-size:12px;color:#656d76}.v{font-size:26px;font-weight:600;font-variant-numeric:tabular-nums}
.ok{color:#1a7f37}.bad{color:#cf222e}.warn{color:#9a6700}
svg{display:block}
#conn{font-size:12px;color:#656d76;margin-top:12px}
</style></head><body>
<h1>EasyCAT + ESP32 編碼器狀態</h1>
<div class="row">
<div class="card"><svg width="170" height="170" viewBox="-85 -85 170 170">
<circle r="78" fill="#f4f5f7" stroke="#d0d7de" stroke-width="2"/>
<line id="nd" x1="0" y1="0" x2="0" y2="-68" stroke="#0969da" stroke-width="5" stroke-linecap="round"/>
<circle r="5" fill="#0969da"/></svg></div>
<div class="card"><div class="k">角度</div><div class="v" id="deg">-</div>
<div class="k" style="margin-top:10px">原始值 (0-4095)</div><div class="v" id="raw">-</div></div>
<div class="card"><div class="k">多圈位置 (counts)</div><div class="v" id="pos">-</div>
<div class="k" style="margin-top:10px">圈數</div><div class="v" id="turns">-</div></div>
</div>
<div class="row" style="margin-top:12px">
<div class="card"><div class="k">ESP32</div><div class="v" id="alive">-</div></div>
<div class="card"><div class="k">EtherCAT</div><div class="v" id="ecat">-</div></div>
<div class="card"><div class="k">磁鐵</div><div class="v" id="mag">-</div></div>
<div class="card"><div class="k">AGC / I2C 錯誤</div><div class="v" id="agc">-</div></div>
</div>
<div id="conn">-</div>
<script>
var S={1:"INIT",2:"PREOP",3:"BOOT",4:"SAFEOP",8:"OP"};
function g(i){return document.getElementById(i)}
function put(i,h,c){var e=g(i);e.innerHTML=h;e.className="v "+(c||"")}
function tick(){
fetch("/s",{cache:"no-store"}).then(function(r){return r.json()}).then(function(j){
var deg=j.raw*360/4096;
g("nd").setAttribute("transform","rotate("+deg.toFixed(1)+")");
put("deg",deg.toFixed(1)+"&deg;");put("raw",j.raw);
put("pos",j.pos);put("turns",(j.pos/4096).toFixed(3));
put("alive",j.alive?"運作中":"無心跳",j.alive?"ok":"bad");
put("ecat",S[j.ecat]||j.ecat,j.ecat==8?"ok":"bad");
var m=j.st&1?["無感測器","bad"]:j.st&16?["太弱","warn"]:j.st&8?["太強","warn"]:j.st&32?["正常","ok"]:["未偵測","bad"];
put("mag",m[0],m[1]);put("agc",j.agc+" / "+j.err);
g("conn").innerHTML="更新 "+new Date().toLocaleTimeString()+" &middot; 請求 "+j.hb;
setTimeout(tick,200)}).catch(function(){g("conn").innerHTML="連線中斷，重試中";setTimeout(tick,1000)})}
tick();
</script></body></html>"""


def to_ascii(s):
    out = []
    for ch in s:
        o = ord(ch)
        out.append(ch if o < 128 else "&#%d;" % o)
    return "".join(out).replace("\n", "")


html = to_ascii(PAGE)
if "'" in html or "$" in html:
    raise Exception("page contains ' or $, which IEC string literals would mangle")
html_len = len(html)

DECL = """PROGRAM PRG_EcatEspHttp
VAR CONSTANT
    HTML_LEN        : UDINT := %(n)d;
END_VAR
VAR
    sHtml           : STRING(%(n1)d) := '%(html)s';
    sHdrHtml        : STRING(200) := 'HTTP/1.1 200 OK$R$NContent-Type: text/html; charset=utf-8$R$NCache-Control: no-store$R$NConnection: close$R$N$R$N';
    sHdrJson        : STRING(200) := 'HTTP/1.1 200 OK$R$NContent-Type: application/json$R$NCache-Control: no-store$R$NConnection: close$R$N$R$N';
    sHdr404         : STRING(120) := 'HTTP/1.1 404 Not Found$R$NContent-Length: 0$R$NConnection: close$R$N$R$N';
    sJson           : STRING(255);

    ipAny           : NBS.IPv4Address := (ipAddress:='0.0.0.0');
    fbServer        : NBS.TCP_Server := (itfIPAddress:=ipAny, uiPort:=%(port)d, xEnable:=TRUE, udiMaxConnections:=1);
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
END_VAR
""" % {"n": html_len, "n1": html_len + 1, "html": html, "port": PORT}

IMPL = """fbServer();
itfServer := fbServer.itfServer;
fbConn(itfServer := itfServer, xEnable := (itfServer <> 0) AND NOT xReset);

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
            sJson := CONCAT(sJson, ',"bx":');
            sJson := CONCAT(sJson, REAL_TO_STRING(PRG_SimIo.rBtmX));
            sJson := CONCAT(sJson, ',"by":');
            sJson := CONCAT(sJson, REAL_TO_STRING(PRG_SimIo.rBtmY));
            sJson := CONCAT(sJson, '}');
            eTx := fbConn.Write(ADR(sHdrJson), INT_TO_UDINT(LEN(sHdrJson)));
            eTx := fbConn.Write(ADR(sJson), INT_TO_UDINT(LEN(sJson)));
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
IF xReset AND NOT fbConn.xActive THEN
    xReset := FALSE;
    xResponded := FALSE;
END_IF
"""


def t(v):
    try:
        if isinstance(v, unicode):
            return v.encode("utf-8", "replace")
        return str(v)
    except Exception:
        return "?"


proj = projects.primary
app = proj.active_application

pou = None
for o in app.get_children():
    if t(o.get_name()) == NAME:
        pou = o
if pou is None:
    pou = app.create_pou(NAME, PouType.Program)
    print("created %s" % NAME)
pou.textual_declaration.replace(DECL)
pou.textual_implementation.replace(IMPL)
print("page: %d bytes" % html_len)

task = None
for tc in proj.find("Task Configuration", True) or []:
    for tk in tc.get_children():
        if t(tk.get_name()) == TASK:
            task = tk
calls = [t(getattr(c, "name", c)) for c in task.pous]
if NAME not in calls:
    task.pous.add(NAME)
    print("added %s to %s (was %s)" % (NAME, TASK, calls))

system.clear_messages(BUILD)
app.generate_code()
errs = 0
for m in system.get_message_objects(BUILD):
    if "error" in str(getattr(m, "severity", "")).lower():
        errs += 1
        print("  [ERR] %s" % t(getattr(m, "text", m)))
print("build errors: %d" % errs)

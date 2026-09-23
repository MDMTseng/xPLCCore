PROGRAM PRG_MbProbe
VAR
    xRun        : BOOL;             // write TRUE to execute iChannel once
    iChannel    : INT := 0;         // configured channel index
    fbCh        : IoDrvModbus.ModbusChannel;
    xDone       : BOOL;             // latched result of the last run
    xError      : BOOL;
    eErr        : IoDrvModbus.MB_ErrorCodes;
    uiSlaves    : UINT;             // port: communicating slaves
    xAllOk      : BOOL;             // port: all slaves ok
    udiRuns     : UDINT;
    udiDone     : UDINT;
    udiErrors   : UDINT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
fbCh(slave := SmartBowlFeeder, xExecute := xRun, iChannelIndex := iChannel);

uiSlaves := Modbus_Client_COM_Port.uiNumberOfCommunicatingSlaves;
xAllOk := Modbus_Client_COM_Port.xAllSlavesOk;

IF xRun AND (fbCh.xDone OR fbCh.xError) THEN
    xDone := fbCh.xDone;
    xError := fbCh.xError;
    eErr := fbCh.ModbusError;
    udiRuns := udiRuns + 1;
    IF fbCh.xDone THEN udiDone := udiDone + 1; END_IF
    IF fbCh.xError THEN udiErrors := udiErrors + 1; END_IF
    xRun := FALSE;
END_IF

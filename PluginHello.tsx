import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { encode, decodeMultiStream } from '@msgpack/msgpack';
import { ModbusRTU } from './lib/ModbusRTU';
import { ControlPage } from './components/ControlPage';
import type { COMCtrlObj } from './types';
import { useTcpStringConnection } from './hooks/useTcpStringConnection';
import { t, type UILang } from './i18n';
import { hasHarnessAction, dispatchHarnessAction, listHarnessActions } from './harness/registry';

// Bump when shipping changes to the TCP/msgpack dispatcher or PLC protocol.
// Shown as a pill next to the app title so you can tell at a glance whether
// the dev server reloaded.
const __UI_BUILD_TAG__ = 'v0.4.0-harness-registry';

type AppPacket = {
  tl: string;
  target_id: number;
  is_end_of_group: boolean;
  content: {
    metadata_str: string;
    binary_bytes?: Uint8Array;
    metadata_parsed?: any;
  };
};

// Dynamic import for Node.js modules - will only be available at runtime in Electron
let net: any = null;
let Socket: any = null;
let createConnection: any = null;
let createServer: any = null;
let BufferCtor: any = null;

// Try to import Node.js modules dynamically
try {
  // This will only work at runtime in Electron, not during build
  if (typeof window !== 'undefined' && (window as any).require) {
    // Electron environment
    net = (window as any).require('net');
    Socket = net.Socket;
    createConnection = net.createConnection;
    createServer = net.createServer;
    try { BufferCtor = (window as any).require('buffer').Buffer } catch { }
  }
} catch (error) {
  console.log('Node.js modules not available during build time');
}

type JsonExtractionResult = {
  messages: string[];
  remainder: string;
};
// Extracts complete JSON objects from a stream, preserving any incomplete remainder for the next chunk.
const extractJsonMessages = (buffer: string): JsonExtractionResult => {
  const messages: string[] = [];
  let depth = 0;
  let startIndex = -1;
  let inString = false;
  let escape = false;

  for (let i = 0; i < buffer.length; i += 1) {
    const char = buffer[i];

    if (startIndex === -1) {
      if (char === '{') {
        startIndex = i;
        depth = 1;
        inString = false;
        escape = false;
      }
      continue;
    }

    if (inString) {
      if (escape) {
        escape = false;
      } else if (char === '\\') {
        escape = true;
      } else if (char === '"') {
        inString = false;
      }
      continue;
    }

    if (char === '"') {
      inString = true;
      continue;
    }

    if (char === '{') {
      depth += 1;
      continue;
    }

    if (char === '}') {
      depth -= 1;
      if (depth === 0 && startIndex !== -1) {
        messages.push(buffer.slice(startIndex, i + 1));
        startIndex = -1;
      }
    }
  }

  let remainder = '';
  if (startIndex !== -1) {
    remainder = buffer.slice(startIndex);
  }

  return { messages, remainder };
};




export const PluginHello: React.FC<{
  prj_path: string,

  lib_path: string,
  UI_path: string,
  plugin_name: string,
  instance_id: string,
  api_sendData: (outgoingData: AppPacket) => Promise<AppPacket[]>,
  api_setDataChannel: (outgoingData: AppPacket, stream_group_id: number, callback: (data: AppPacket[]) => void) => Promise<AppPacket[]>,
}> = ({ prj_path, plugin_name, instance_id, lib_path, UI_path, api_sendData, api_setDataChannel }) => {
  const env_path=prj_path+"/"+instance_id;




  const [tcpAvailable, setTcpAvailable] = useState(false);
  // Enable flags to drive effects
  const [tcpClientEnabled, setTcpClientEnabled] = useState(false);

  // TCP Client states
  const [tcpConnected, setTcpConnected] = useState(false);
  const [tcpHost, setTcpHost] = useState('localhost');
  const [tcpPort, setTcpPort] = useState(8080);
  const [tcpClearAfterSend, setTcpClearAfterSend] = useState(false);
  const [tcpReceivedMessages, setTcpReceivedMessages] = useState<string[]>([]);
  const clientInfoKey = `${instance_id}_TCP_client_info`;


  // UDP removed
  const _this = useRef<any>({
    counter: 0,

    TX_id_COUNTER: 0,
    RX_lookup: {

    },


    VP_TX_id_COUNTER: 0,
    VP_RX_lookup: {
    },
  }).current;


  
  const [connectTCP2, setConnectTCP2] = useState(false);
  const [FVibStatus, setFVibStatus] = useState(0);
  const [tcp2Host, setTcp2Host] = useState('localhost');
  const [tcp2Port, setTcp2Port] = useState(7950);
  const [modbusPort, setModbusPort] = useState('COM3');
  const [modbusBaudRate, setModbusBaudRate] = useState(19200);
  const [connectionModalOpen, setConnectionModalOpen] = useState(false);
  const [uiLang, setUiLang] = useState<UILang>('zh');

  
  useEffect(() => {
    
  
    let path = (window as any).require('path');
    let UI_folder = path.dirname(UI_path);
    let script_path = path.join(UI_folder, 'script', 'serial_ctrl.py');
    let venv_path = path.join(UI_folder, 'script', 'venv');

    let modbusClient = new ModbusRTU(script_path, venv_path);

    function flexVibCtrl(slaveId: number, modbusClient:ModbusRTU){
      let isConnected = false;
      modbusClient.onConnect = ()=>{
        console.log("Modbus connected",modbusClient);
        isConnected = true;
        setFVibStatus(2);
        _this.modbusClient=modbusClient;
      }
      modbusClient.onDisconnect = ()=>{
        console.log("Modbus disconnected");
        isConnected = false;
        setFVibStatus(0);
      }
      modbusClient.onError = (error:string)=>{
        console.log("Modbus error",error);
      }
      return {
        connect:(port:string, baudRate:number)=>{
          modbusClient.connect(port, baudRate);
        },
        disconnect:()=>{
          modbusClient.disconnect();
        },
        isConnected:()=>{
          return isConnected;
        },
        
        von:async(idx:number)=>{
          console.log("von",idx);
          return modbusClient.writeReg(slaveId,idx,0x01);
        },
        voff:async(idx:number)=>{
          return modbusClient.writeReg(slaveId,idx,0x00);
        },


        top_light_on:async()=>{
          return modbusClient.writeReg(slaveId,0xc,0x8);
        },
        top_light_off:async()=>{
          return modbusClient.writeReg(slaveId,0xc,0x00);
        },
        
      }
    }

    let _flexVibCtrl=flexVibCtrl(0x1,modbusClient);
    _this.flexVibCtrl=_flexVibCtrl;
    return ()=>{
      modbusClient.disconnect();

    };
  }, [UI_path]);
  const FlexVibCtrl=_this.flexVibCtrl;
  const tcpRxBufferRef = useRef<string>('');

  const { status: tcp2Status, send: sendTcp2 } = useTcpStringConnection(
    connectTCP2,
    tcp2Host,
    tcp2Port,
    (rxData: string) => {
      console.log('rx_data:1:', rxData);

      tcpRxBufferRef.current += rxData;
      const { messages, remainder } = extractJsonMessages(tcpRxBufferRef.current);
      tcpRxBufferRef.current = remainder;

      let handled = false;
      let unresolvedTrigger = false;

      for (const jsonChunk of messages) {
        try {
          const payload = JSON.parse(jsonChunk);
          const { trig_id: triggerId, data } = payload;
          if (!(triggerId in _this.VP_RX_lookup)) {
            console.log('trig_id not found', triggerId, 'rx_data', _this.VP_RX_lookup);
            unresolvedTrigger = true;
            continue;
          }
          _this.VP_RX_lookup[triggerId].resolve(data);
          if (_this.VP_RX_lookup[triggerId]._PERSIST_ !== true) {
            delete _this.VP_RX_lookup[triggerId];
          }
          handled = true;
        } catch (error) {
          console.log('rx_data:2:error', error, 'raw', jsonChunk);
        }
      }

      if (unresolvedTrigger) {
        return false;
      }

      return handled || messages.length === 0;
    },
  );


  // UDP removed
  const tcpSocketRef = useRef<any>(null);
  const tcpServerRef = useRef<any>(null);
  const clientMessagesRef = useRef<HTMLDivElement | null>(null);
  const serverMessagesRef = useRef<HTMLDivElement | null>(null);
  // Optional: keep timers responsive in background by playing a looping silent audio
  const bgAudioRef = useRef<HTMLAudioElement | null>(null);


  // Check if protocols are available
  useEffect(() => {
    const checkProtocolAvailability = async () => {
      try {
        if (typeof window !== 'undefined' && (window as any).require) {
          // Check TCP availability
          const netModule = (window as any).require('net');
          if (netModule && netModule.createConnection && netModule.createServer) {
            setTcpAvailable(true);
            net = netModule;
            Socket = netModule.Socket;
            createConnection = netModule.createConnection;
            createServer = netModule.createServer;
          }
          // UDP removed
        }
      } catch (error) {
        console.log('Protocols not available:', error);
        setTcpAvailable(false);
      }
    };

    checkProtocolAvailability();
  }, []);

  // Load persisted TCP client info
  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const saved = window.localStorage.getItem(clientInfoKey);
        if (saved) {
          const parsed = JSON.parse(saved);
          if (parsed && typeof parsed.host === 'string') setTcpHost(parsed.host);
          if (parsed && typeof parsed.port !== 'undefined') setTcpPort(parseInt(parsed.port.toString()) || 8080);
        }
      }
    } catch { }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clientInfoKey]);

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const tcp2Key = `${instance_id}_TCP2_client_info`;
        const modbusKey = `${instance_id}_MODBUS_client_info`;
        const savedTcp2 = window.localStorage.getItem(tcp2Key);
        const savedModbus = window.localStorage.getItem(modbusKey);
        if (savedTcp2) {
          const parsed = JSON.parse(savedTcp2);
          if (parsed && typeof parsed.host === 'string') setTcp2Host(parsed.host);
          if (parsed && typeof parsed.port !== 'undefined') setTcp2Port(parseInt(parsed.port.toString()) || 7950);
        }
        if (savedModbus) {
          const parsed = JSON.parse(savedModbus);
          if (parsed && typeof parsed.port === 'string') setModbusPort(parsed.port);
          if (parsed && typeof parsed.baudRate !== 'undefined') setModbusBaudRate(parseInt(parsed.baudRate.toString()) || 19200);
        }
      }
    } catch { }
  }, [instance_id]);

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const tcp2Key = `${instance_id}_TCP2_client_info`;
        window.localStorage.setItem(tcp2Key, JSON.stringify({ host: tcp2Host, port: tcp2Port }));
      }
    } catch { }
  }, [instance_id, tcp2Host, tcp2Port]);

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const modbusKey = `${instance_id}_MODBUS_client_info`;
        window.localStorage.setItem(modbusKey, JSON.stringify({ port: modbusPort, baudRate: modbusBaudRate }));
      }
    } catch { }
  }, [instance_id, modbusPort, modbusBaudRate]);

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const savedLang = window.localStorage.getItem(`${instance_id}_UI_lang`);
        if (savedLang === 'en' || savedLang === 'zh') {
          setUiLang(savedLang);
        }
      }
    } catch { }
  }, [instance_id]);

  useEffect(() => {
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem(`${instance_id}_UI_lang`, uiLang);
      }
    } catch { }
  }, [instance_id, uiLang]);

  // TCP Client lifecycle: managed by effect
  useEffect(() => {
    if (!tcpClientEnabled) {
      if (tcpSocketRef.current) {
        tcpSocketRef.current.end();
        tcpSocketRef.current = null;
      }
      setTcpConnected(false);
      return;
    }

    if (!createConnection) {
      console.error('TCP not available');
      setTcpClientEnabled(false);
      return;
    }

    try {
      const socket = createConnection({
        host: tcpHost,
        port: parseInt(tcpPort.toString())
      });

      socket.on('connect', () => {
        console.log('TCP Connected to', tcpHost + ':' + tcpPort);
        setTcpConnected(true);
        tcpSocketRef.current = socket;
        try {
          if (typeof window !== 'undefined' && window.localStorage) {
            window.localStorage.setItem(clientInfoKey, JSON.stringify({ host: tcpHost, port: tcpPort }));
          }
        } catch { }
      });


      async function handleConnectionRX(socket: any) {
        console.log("Client connected. Starting to process messages...");

        try {
          // Treat the socket as a stream of MessagePack objects!
          for await (const obj of decodeMultiStream(socket)) {
            // --- Your original logic goes here ---
            // This code block will run for each complete message received.

            // console.log("Successfully parsed object:", obj);

            const message = obj as any; // Cast for convenience
            if (message && message.id) {
              if (message.id in _this.RX_lookup) {
                const entry = _this.RX_lookup[message.id];
                if (entry.timer) { clearTimeout(entry.timer); entry.timer = null; }
                // NAK support: a reply with ack===false rejects the caller
                // instead of resolving, so `await` sites see the failure.
                // (PLC schema: {id, ack:bool, err?:string}.)
                if (message.ack === false) {
                  try { entry.reject(new Error(message.err || `nak (id=${message.id}, cmd=${entry?.tx_data?.cmd ?? entry?.tx_data?.type})`)); } catch {}
                } else {
                  entry.resolve(message);
                }
                if (entry._PERSIST_ !== true) {
                  delete _this.RX_lookup[message.id];
                }
              }
            }

            // const display = `MsgPack: ${JSON.stringify(message)}`;
            // console.log('TCP Frame Received:', display);
            // setTcpReceivedMessages(prev => [...prev, `Received: ${display}`]);
          }
        } catch (err) {
          // This will catch socket errors or fatal decoding errors
          console.error("Connection error:", err);
        } finally {
          console.log("Client disconnected.");
        }
      }
      handleConnectionRX(socket);

      const rejectAllPending = (reason: string) => {
        const lookup = _this.RX_lookup;
        for (const key of Object.keys(lookup)) {
          const entry = lookup[key];
          if (!entry) continue;
          if (entry._PERSIST_ === true) continue; // keep callback subscriptions
          if (entry.timer) { clearTimeout(entry.timer); entry.timer = null; }
          try { entry.reject(new Error(reason)); } catch {}
          delete lookup[key];
        }
      };

      socket.on('error', (err: any) => {
        console.error('TCP Error:', err);
        setTcpConnected(false);
        tcpSocketRef.current = null;
        rejectAllPending(`tcp error: ${err?.message || err}`);
      });

      socket.on('close', () => {
        console.log('TCP Connection closed');
        setTcpConnected(false);
        tcpSocketRef.current = null;
        rejectAllPending('tcp connection closed');
      });
    } catch (error) {
      console.error('Failed to create TCP connection:', error);
    }

    return () => {
      if (tcpSocketRef.current) {
        tcpSocketRef.current.end();
        tcpSocketRef.current = null;
      }
      setTcpConnected(false);
    };
  }, [tcpClientEnabled, tcpHost, tcpPort]);

  // Keep timers responsive: start a looping silent audio once when app mounts
  if(false)useEffect(() => {
    try {
      if (!bgAudioRef.current) {
        const audio = new Audio(
          // 1-second silent WAV, base64-encoded
          'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA='
        );
        audio.loop = true;
        audio.muted = true;
        bgAudioRef.current = audio;
      }
      // Play (may require user interaction depending on environment/autoplay policy)
      bgAudioRef.current?.play().catch(() => {
        // Ignore autoplay errors; app will still function, just with default timer behavior.
      });
    } catch {
      // Best-effort only; ignore any audio setup errors
    }

    return () => {
      if (bgAudioRef.current) {
        bgAudioRef.current.pause();
      }
    };
  }, []);


  // Track whether any MsgPack was sent recently; used to skip HB when traffic is busy
  const sentFlagRef = useRef<boolean>(false);

  const DEFAULT_TCP_REPLY_TIMEOUT_MS = 5000;
  const sendTcpMsgPack = useCallback((data: any, await_tracking: boolean = true, timeout_ms: number = DEFAULT_TCP_REPLY_TIMEOUT_MS) : Promise<any>|boolean => {
    if (!tcpSocketRef.current) return false;

    // if(data?.cmd!=="GET_DIGITAL_INPUT"){
    //   console.log("sendTcpMsgPack",data);
    // }
    let promise: Promise<any> | undefined = undefined;
    try {

      let res_fn;
      let rej_fn;
      if (await_tracking) {
        _this.TX_id_COUNTER++;
        while (_this.TX_id_COUNTER in _this.RX_lookup) {
          _this.TX_id_COUNTER++;
        }
        let ava_id = _this.TX_id_COUNTER;
        data.id = ava_id;

        promise = new Promise((resolve, reject) => {
          res_fn = resolve;
          rej_fn = reject;
        });

        const entry: any = {
          tx_data: data,
          resolve: res_fn,
          reject: rej_fn,
          timer: null,
        };
        if (timeout_ms > 0) {
          entry.timer = setTimeout(() => {
            const pending = _this.RX_lookup[ava_id];
            if (pending && pending._PERSIST_ !== true) {
              delete _this.RX_lookup[ava_id];
              try { pending.reject(new Error(`tcp reply timeout (id=${ava_id}, cmd=${data?.cmd ?? data?.type})`)); } catch {}
            }
          }, timeout_ms);
        }
        _this.RX_lookup[ava_id] = entry;
      }
      // console.log("sendTcpMsgPack",data);





      const packed = encode(data) as Uint8Array;
      // Write binary MsgPack payload using Buffer from runtime
      const buf = BufferCtor ? BufferCtor.from(packed) : packed;
      tcpSocketRef.current.write(buf);
      // mark that we sent something in this period
      sentFlagRef.current = true;

      if (promise !== undefined) {
        return promise;
      }
      return true;
    } catch (e: any) {
      setTcpReceivedMessages(prev => [...prev, `MsgPack send error: ${e?.message || e}`]);
      console.error('MsgPack send error:', e);
    }
    if (await_tracking) {
      throw new Error('sending error');
    }
    return false;
  }, [tcpClearAfterSend]);

  // Periodic keepalive to PLC over MsgPack TCP channel
  const keepaliveTimerRef = useRef<any>(null);
  useEffect(() => {
    if (tcpConnected) {
      if (!keepaliveTimerRef.current) {
        keepaliveTimerRef.current = window.setInterval(() => {
          try {
            // Only send keepalive if no other traffic was sent in this period
            if (!sentFlagRef.current) {
              // Fire-and-forget keepalive; no need to await tracking
              sendTcpMsgPack({ type: 'M', cmd: 'KEEPALIVE' }, false);
            }
            // reset flag for next interval window
            sentFlagRef.current = false;
          } catch (e) {
            console.error('Keepalive send error:', e);
          }
        }, 1000);
      }
    } else if (keepaliveTimerRef.current) {
      clearInterval(keepaliveTimerRef.current);
      keepaliveTimerRef.current = null;
    }

    return () => {
      if (keepaliveTimerRef.current) {
        clearInterval(keepaliveTimerRef.current);
        keepaliveTimerRef.current = null;
      }
    };
  }, [tcpConnected, sendTcpMsgPack]);


  //this will register a callback for a specific receiving target ID
  //cb undefined means unregister
  const regTcpMsgCB = useCallback((tarID: number, cb: ((data: any) => void) | undefined) : boolean=> {
    if (!tcpSocketRef.current) return false;
    console.log("regTcpMsgCB",tarID,cb,_this.RX_lookup);
    //no registered and user wanna unregister
    if(_this.RX_lookup[tarID]==undefined && cb==undefined){
      
      return false;
    }

    //registered and user wanna register again
    if(_this.RX_lookup[tarID]!=undefined && cb!=undefined){
      //need to unregister first
      console.log("unregistering",tarID);
      return false;
    }

    if(_this.RX_lookup[tarID]!==undefined && cb==undefined){//unregister
      
      delete _this.RX_lookup[tarID];
      return true;
    }



    //register a new callback
    _this.RX_lookup[tarID] = {
      resolve: cb,
      _PERSIST_:true,
    }
      // console.log("sendTcpMsgPack",data);


    return true;
  }, [tcpClearAfterSend]);






  const VP_sendTcpMsgPack = useCallback((data: any): Promise<any> => {
    if (tcp2Status !== 2) {
      throw new Error('not connected');
    }
    let promise: Promise<any> | undefined = undefined;
    try {

      let res_fn;
      let rej_fn;
      
    
      _this.VP_TX_id_COUNTER++;
      while (_this.VP_TX_id_COUNTER in _this.VP_RX_lookup) {
        _this.VP_TX_id_COUNTER++;
      }
      let ava_id = _this.VP_TX_id_COUNTER;

      data.id=ava_id;
      promise = new Promise((resolve, reject) => {
        res_fn = resolve;
        rej_fn = reject;
      });

      _this.VP_RX_lookup[ava_id] = {
        type: data.type,
        trigger_id: ava_id,
        RX_str_arr: [],
        resolve: res_fn,
        reject: rej_fn
      }
    
      // console.log("sendTcpMsgPack",data);
      sendTcp2(JSON.stringify(data)+";");



      return promise;
    } catch (e: any) {
    }
    throw new Error('sending error');
  }, [sendTcp2, tcp2Status]);

  const VP_regTcpMsgCB = useCallback((tarID: number, cb: ((data: any) => void) | undefined) : boolean=> {
    if (!tcpSocketRef.current) return false;
    // console.log("VP_regTcpMsgCB",tarID,cb,_this.RX_lookup);
    //no registered and user wanna unregister
    if(_this.VP_RX_lookup[tarID]==undefined && cb==undefined){
      
      return false;
    }

    //registered and user wanna register again
    if(_this.VP_RX_lookup[tarID]!=undefined && cb!=undefined){
      //need to unregister first
      console.log("unregistering",tarID);
      return false;
    }

    if(_this.VP_RX_lookup[tarID]!==undefined && cb==undefined){//unregister
      
      delete _this.VP_RX_lookup[tarID];
      return true;
    }



    //register a new callback
    _this.VP_RX_lookup[tarID] = {
      resolve: cb,
      _PERSIST_:true,
    }
      console.log(_this.VP_RX_lookup);


    return true;
  }, [tcpClearAfterSend]);




  // UDP removed

  // ---------------------------------------------------------------------
  // Remote-harness polling loop.
  // A local dev harness (codesys_scripts/remote_harness.py) queues
  // instructions; this loop pulls one per tick and executes it, then POSTs
  // the result. Guarded by the hasHarness flag so production builds never
  // hit a network timer unless the harness is actually reachable.
  // ---------------------------------------------------------------------
  const HARNESS_URL = 'http://127.0.0.1:8127';
  const [harnessEnabled, setHarnessEnabled] = useState<boolean>(() => {
    try { return window.localStorage.getItem('remote_harness_enabled') === '1'; } catch { return false; }
  });
  const harnessBusyRef = useRef<boolean>(false);

  const harnessExecute = useCallback(async (instr: any): Promise<any> => {
    const action = instr.action as string;
    const payload = instr.payload ?? {};
    switch (action) {
      case 'ping':
        return { pong: true, ts: Date.now(), build: __UI_BUILD_TAG__ };
      case 'get_state':
        return {
          tcpConnected,
          tcpHost,
          tcpPort,
          build: __UI_BUILD_TAG__,
          pendingRequests: Object.keys(_this.RX_lookup).length,
        };
      case 'connect_tcp':
        if (typeof payload.host === 'string') setTcpHost(payload.host);
        if (typeof payload.port === 'number' || typeof payload.port === 'string') setTcpPort(parseInt(String(payload.port)));
        setTcpClientEnabled(true);
        return { requested: true };
      case 'disconnect_tcp':
        setTcpClientEnabled(false);
        return { requested: true };
      case 'send_tcp_msgpack': {
        const data = payload.data ?? payload;
        const awaitTracking = payload.await_tracking !== false;
        const timeoutMs = typeof payload.timeout_ms === 'number' ? payload.timeout_ms : undefined;
        const result = (sendTcpMsgPack as any)(data, awaitTracking, timeoutMs);
        if (result instanceof Promise) {
          return await result;
        }
        return { sync: result };
      }
      case 'wait_ms':
        await new Promise((res) => setTimeout(res, Number(payload.ms ?? 0)));
        return { waited_ms: Number(payload.ms ?? 0) };
      case 'list_actions':
        return {
          builtins: ['ping', 'get_state', 'connect_tcp', 'disconnect_tcp', 'send_tcp_msgpack', 'wait_ms', 'list_actions', 'reload'],
          registered: listHarnessActions(),
        };
      case 'reload':
        setTimeout(() => { try { window.location.reload(); } catch {} }, 50);
        return { reloading: true };
      default:
        if (hasHarnessAction(action)) {
          return await dispatchHarnessAction(action, payload);
        }
        throw new Error(`unknown harness action: ${action}`);
    }
  }, [tcpConnected, tcpHost, tcpPort, sendTcpMsgPack]);

  useEffect(() => {
    if (!harnessEnabled) return;
    let stopped = false;
    // Hard cap per instruction. Anything longer is a bug (or a wedged TCP
    // reply). We race harnessExecute against this so busy cannot stick
    // forever even if a Promise never resolves.
    const INSTR_MAX_MS = 15000;
    // Watchdog: if busy has been TRUE longer than this with no progress,
    // force-clear. Covers StrictMode double-mounts and HMR leaving a stale
    // busy=true from a previous effect instance.
    const BUSY_WATCHDOG_MS = 20000;
    let busySince = 0;
    const tick = async () => {
      if (stopped) return;
      if (harnessBusyRef.current) {
        if (busySince && performance.now() - busySince > BUSY_WATCHDOG_MS) {
          console.warn('[harness] busy stuck >20s, force-clearing');
          harnessBusyRef.current = false;
          busySince = 0;
        }
        return;
      }
      try {
        const resp = await fetch(`${HARNESS_URL}/poll`, { method: 'GET' });
        if (!resp.ok) return;
        const instr = await resp.json();
        if (!instr || !instr.action) return;
        harnessBusyRef.current = true;
        busySince = performance.now();
        const started = busySince;
        let ok = true;
        let value: any = undefined;
        let err: string | undefined;
        try {
          value = await Promise.race([
            harnessExecute(instr),
            new Promise((_, rej) => setTimeout(
              () => rej(new Error(`harness instr timeout >${INSTR_MAX_MS}ms (action=${instr.action})`)),
              INSTR_MAX_MS,
            )),
          ]);
        } catch (e: any) {
          ok = false;
          err = e?.message ?? String(e);
        }
        const elapsed_ms = Math.round(performance.now() - started);
        try {
          await fetch(`${HARNESS_URL}/result`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instr_id: instr.instr_id, ok, value, err, elapsed_ms, action: instr.action }),
          });
        } catch { /* harness gone; keep polling */ }
      } catch {
        // harness offline -- stay silent, keep polling
      } finally {
        harnessBusyRef.current = false;
        busySince = 0;
      }
    };
    const id = window.setInterval(tick, 1000);
    return () => {
      stopped = true;
      window.clearInterval(id);
      // Don't leave a stale busy flag for the next effect instance (HMR,
      // StrictMode, dep changes). A tick still in-flight will run its own
      // finally and redundantly clear; that's fine.
      harnessBusyRef.current = false;
    };
  }, [harnessEnabled, harnessExecute]);

  useEffect(() => {
    try { window.localStorage.setItem('remote_harness_enabled', harnessEnabled ? '1' : '0'); } catch {}
  }, [harnessEnabled]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (tcpSocketRef.current) {
        tcpSocketRef.current.end();
      }
      if (tcpServerRef.current) {
        tcpServerRef.current.close();
      }
      // UDP removed
    };
  }, []);

  const COMCtrlObj = useMemo<COMCtrlObj>(
    () => ({
      sendTcpMsgPack,
      regTcpMsgCB,
      VP_regTcpMsgCB,
      VP_sendTcpMsgPack,
      FlexVibCtrl,
    }),
    [FlexVibCtrl, regTcpMsgCB, sendTcpMsgPack, VP_regTcpMsgCB, VP_sendTcpMsgPack],
  );

  const tcp2Connected = tcp2Status === 2;
  const modbusConnected = FVibStatus === 2;
  const allCoreLinksConnected = true||tcpConnected && tcp2Connected && modbusConnected;

  const dotColor = (connected: boolean, connecting: boolean) => {
    if (connected) return '#22c55e';
    if (connecting) return '#f59e0b';
    return '#ef4444';
  };

  const dotStyle = (connected: boolean, connecting: boolean): React.CSSProperties => ({
    width: 14,
    height: 14,
    borderRadius: '50%',
    border: '1px solid #555',
    background: dotColor(connected, connecting),
    display: 'inline-block',
  });

  const sectionCardStyle: React.CSSProperties = {
    border: '1px solid #e5e7eb',
    borderRadius: 12,
    background: '#ffffff',
    padding: 14,
  };

  return (
    <div
      style={{
        padding: 16,
        border: '1px solid #cbd5e1',
        borderRadius: 16,
        background: 'linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%)',
        color: '#111827',
      }}
    >
      <div style={{ ...sectionCardStyle, marginBottom: 12, borderRadius: 14, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '9px 12px', display: 'grid', gridTemplateColumns: 'minmax(0,1fr) auto', gap: 8, alignItems: 'start' }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: '#0f172a', display: 'flex', alignItems: 'center', gap: 6 }}>
              {t(uiLang, 'appTitle')}
              <span
                title="UI build tag — bumps every time the dispatcher/msgpack layer changes"
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: '#065f46',
                  background: '#d1fae5',
                  border: '1px solid #6ee7b7',
                  borderRadius: 6,
                  padding: '1px 6px',
                  letterSpacing: 0.3,
                }}
              >
                {__UI_BUILD_TAG__}
              </span>
              <button
                type="button"
                onClick={() => setHarnessEnabled((v) => !v)}
                title="Toggle remote-harness polling (codesys_scripts/remote_harness.py on :8127)"
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  color: harnessEnabled ? '#7c2d12' : '#334155',
                  background: harnessEnabled ? '#fed7aa' : '#f1f5f9',
                  border: harnessEnabled ? '1px solid #fb923c' : '1px solid #cbd5e1',
                  borderRadius: 6,
                  padding: '1px 6px',
                  letterSpacing: 0.3,
                  cursor: 'pointer',
                }}
              >
                {harnessEnabled ? 'harness ON' : 'harness off'}
              </button>
            </div>
            <div style={{ marginTop: 2, fontSize: 11, color: '#64748b' }}>
              {t(uiLang, 'instanceProject', { instance: instance_id, project: plugin_name })}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, justifySelf: 'end' }}>
            <div style={{ display: 'inline-flex', border: '1px solid #d1d5db', borderRadius: 999, overflow: 'hidden', background: '#ffffff' }}>
              <button
                type="button"
                onClick={() => setUiLang('en')}
                style={{
                  border: 'none',
                  background: uiLang === 'en' ? '#e2e8f0' : 'transparent',
                  color: '#334155',
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                EN
              </button>
              <button
                type="button"
                onClick={() => setUiLang('zh')}
                style={{
                  border: 'none',
                  borderLeft: '1px solid #e5e7eb',
                  background: uiLang === 'zh' ? '#e2e8f0' : 'transparent',
                  color: '#334155',
                  padding: '4px 8px',
                  fontSize: 11,
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                中文
              </button>
            </div>
            <span
              style={{
                borderRadius: 999,
                padding: '5px 10px',
                fontSize: 11,
                fontWeight: 700,
                background: allCoreLinksConnected ? '#dcfce7' : '#fef3c7',
                color: allCoreLinksConnected ? '#166534' : '#92400e',
              }}
            >
              {allCoreLinksConnected ? t(uiLang, 'systemReady') : t(uiLang, 'waitingLinks')}
            </span>
            <button
              type="button"
              title="Toggle connection setup"
              onClick={() => tcpAvailable && setConnectionModalOpen((prev) => !prev)}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 8,
                border: '1px solid #d1d5db',
                borderRadius: 999,
                background: '#ffffff',
                padding: '5px 10px',
                cursor: tcpAvailable ? 'pointer' : 'not-allowed',
                opacity: tcpAvailable ? 1 : 0.6,
              }}
            >
              <span style={dotStyle(tcpConnected, tcpClientEnabled && !tcpConnected)} />
              <span style={dotStyle(tcp2Connected, connectTCP2 && !tcp2Connected)} />
              <span style={dotStyle(modbusConnected, false)} />
              <span style={{ fontSize: 11, color: '#475569', fontWeight: 700 }}>
                {connectionModalOpen ? t(uiLang, 'hide') : t(uiLang, 'setup')}
              </span>
            </button>
          </div>
        </div>

        {!tcpAvailable ? (
          <div style={{ borderTop: '1px solid #e5e7eb', background: '#f8fafc', padding: '8px 10px', fontSize: 11, color: '#9a3412' }}>
            {t(uiLang, 'tcpUnavailable')}
          </div>
        ) : null}

        {connectionModalOpen && tcpAvailable && (
          <div style={{ borderTop: '1px solid #e5e7eb', background: '#f8fafc', padding: '8px 10px' }}>
            <div style={{ marginBottom: 10, fontSize: 11, color: '#64748b' }}>
              {t(uiLang, 'linkHelp')}
            </div>

            <div style={{ marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid #e5e7eb' }}>
              <strong style={{ color: '#1f2937', fontSize: 12 }}>{t(uiLang, 'tcpPlc')}</strong>
              <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  type="text"
                  placeholder={t(uiLang, 'host')}
                  value={tcpHost}
                  onChange={(e) => setTcpHost(e.target.value)}
                  style={{ marginRight: 8, padding: 7, width: 180, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                <input
                  type="number"
                  placeholder={t(uiLang, 'port')}
                  value={tcpPort}
                  onChange={(e) => setTcpPort(parseInt(e.target.value) || 8080)}
                  style={{ padding: 7, width: 100, marginRight: 8, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                {!tcpClientEnabled ? (
                  <button onClick={() => setTcpClientEnabled(true)}>{t(uiLang, 'connect')}</button>
                ) : (
                  <button onClick={() => setTcpClientEnabled(false)} style={{ backgroundColor: '#ff6b6b' }}>{t(uiLang, 'disconnect')}</button>
                )}
              </div>
            </div>

            <div style={{ marginBottom: 10, paddingBottom: 10, borderBottom: '1px solid #e5e7eb' }}>
              <strong style={{ color: '#1f2937', fontSize: 12 }}>{t(uiLang, 'tcp2Vision')}</strong>
              <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  type="text"
                  placeholder={t(uiLang, 'host')}
                  value={tcp2Host}
                  onChange={(e) => setTcp2Host(e.target.value)}
                  style={{ marginRight: 8, padding: 7, width: 180, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                <input
                  type="number"
                  placeholder={t(uiLang, 'port')}
                  value={tcp2Port}
                  onChange={(e) => setTcp2Port(parseInt(e.target.value) || 7950)}
                  style={{ padding: 7, width: 100, marginRight: 8, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                {!connectTCP2 ? (
                  <button onClick={() => setConnectTCP2(true)}>{t(uiLang, 'connect')}</button>
                ) : (
                  <button onClick={() => setConnectTCP2(false)} style={{ backgroundColor: '#ff6b6b' }}>{t(uiLang, 'disconnect')}</button>
                )}
              </div>
            </div>

            <div>
              <strong style={{ color: '#1f2937', fontSize: 12 }}>{t(uiLang, 'modbusFeeder')}</strong>
              <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input
                  type="text"
                  placeholder={t(uiLang, 'comPort')}
                  value={modbusPort}
                  onChange={(e) => setModbusPort(e.target.value)}
                  style={{ marginRight: 8, padding: 7, width: 120, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                <input
                  type="number"
                  placeholder={t(uiLang, 'baudRate')}
                  value={modbusBaudRate}
                  onChange={(e) => setModbusBaudRate(parseInt(e.target.value) || 19200)}
                  style={{ padding: 7, width: 130, marginRight: 8, borderRadius: 8, border: '1px solid #cbd5e1' }}
                />
                {FVibStatus !== 2 ? (
                  <button onClick={() => FlexVibCtrl.connect(modbusPort, modbusBaudRate)}>{t(uiLang, 'connect')}</button>
                ) : (
                  <button onClick={() => FlexVibCtrl.disconnect()} style={{ backgroundColor: '#ff6b6b' }}>{t(uiLang, 'disconnect')}</button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {allCoreLinksConnected && (
        <ControlPage
          env_path={env_path}
          lib_path={lib_path}
          UI_path={UI_path}
          COMCtrlObj={COMCtrlObj}
          uiLang={uiLang}
        />
      )}

      {tcpConnected && !allCoreLinksConnected && (
        <div style={{ ...sectionCardStyle, color: '#9a3412', background: '#fff7ed', borderColor: '#fdba74', fontWeight: 700 }}>
          {t(uiLang, 'controlsLocked')}
        </div>
      )}

      {/* Original Streaming Controls
      <button onClick={async()=>{
        let ret_data3 = await apiSendExchangeData({ "command":"start_streaming"})
        console.log(ret_data3);
      }}>Start Streaming</button>
      <button onClick={async()=>{
        let ret_data3 = await apiSendExchangeData({ "command":"stop_streaming"})
        console.log(ret_data3);
      }}>Stop Streaming</button>
      <br/>
      {image && <img src={image.src} alt="image" />} */}

    </div>
  )
}

export default PluginHello


//{"type":"SYS","cmd":"GA_EV","ev":0,"id":2093489023}
//192.168.1.70:8123

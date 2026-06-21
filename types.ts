export type TcpMessageCallback = (data: any) => void;

export type COMCtrlObj = {
  regTcpMsgCB: (targetId: number, callback: TcpMessageCallback | undefined) => boolean;
  // PLC channel: msgpack on 192.168.1.70:8125. Supports fire-and-forget
  // (returns boolean when waitForTracking=false) and per-call timeout.
  sendTcpMsgPack: (data: any, waitForTracking?: boolean, timeout_ms?: number) => Promise<any> | boolean;
  VP_regTcpMsgCB: (targetId: number, callback: TcpMessageCallback | undefined) => boolean;
  // Vision Plugin channel: JSON `;`-delimited on localhost:7950. Always
  // request/reply via auto-stamped `id`; no fire-and-forget mode and no
  // per-call timeout (callers rely on upstream `delay()` aborts). See
  // doc/2-contracts/vision_contract.md.
  VP_sendTcpMsgPack: (data: any) => Promise<any>;
  FlexVibCtrl: any;
};

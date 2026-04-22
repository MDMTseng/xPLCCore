export type TcpMessageCallback = (data: any) => void;

export type COMCtrlObj = {
  regTcpMsgCB: (targetId: number, callback: TcpMessageCallback | undefined) => boolean;
  sendTcpMsgPack: (data: any, waitForTracking?: boolean) => Promise<any> | boolean;
  VP_regTcpMsgCB: (targetId: number, callback: TcpMessageCallback | undefined) => boolean;
  VP_sendTcpMsgPack: ( data: any) => Promise<any>;
  FlexVibCtrl: any;
};


let spawn: any = null;
let path: any = null;

if (typeof window !== 'undefined' && (window as any).require) {
  try {
    const childProcess = (window as any).require('child_process');
    spawn = childProcess.spawn;
    path = (window as any).require('path');
  } catch (error) {
    console.error("Failed to load Node.js modules. Make sure you're in an Electron environment.", error);
  }
}

export class ModbusRTU {

  private id: string="";
  private script_path: string;
  private venv_path: string;
  private childProcess: any | null = null;
  private _isOpen = false;
  private pendingRequests = new Map<string, { resolve: (value: any) => void, reject: (reason?: any) => void }>();
  private requestIdCounter = 0;

  public onConnect: (() => void) | null = null;
  public onDisconnect: (() => void) | null = null;
  public onData: ((data: any) => void) | null = null;
  public onError: ((error: string) => void) | null = null;

  constructor(script_path: string, venv_path: string) {
    this.id=""+Math.random();
    console.log("new ModbusRTU:",this.id);
    this.script_path = script_path;
    this.venv_path = venv_path;
  }

  get isOpen(): boolean {
    return this._isOpen;
  }

  async listPorts(): Promise<any[]> {
    return new Promise((resolve, reject) => {
      if (!spawn || !path) {
        return reject(new Error("Required Node.js modules are not available."));
      }

      const pythonExecutable = path.join(this.venv_path, 'Scripts', 'python.exe');
      const process = spawn(pythonExecutable, [this.script_path, 'list_ports']);
      
      let portData = '';
      process.stdout.on('data', (chunk: Buffer) => {
        portData += chunk.toString();
      });

      process.stderr.on('data', (data: Buffer) => {
        console.error(`Python script error (list_ports): ${data.toString()}`);
      });

      process.on('close', (code: number) => {
        if (code === 0) {
          try {
            resolve(JSON.parse(portData));
          } catch (err) {
            reject(new Error('Error parsing port list from Python script.'));
          }
        } else {
          reject(new Error(`Python script (list_ports) exited with code ${code}`));
        }
      });
    });
  }

  connect(port: string, baudRate: number): void {
    if (this.childProcess) {
        this.disconnect();
    }
    if (!spawn || !path) {
        console.error("Required Node.js modules are not available.");
        return;
    }

    const pythonExecutable = path.join(this.venv_path, 'Scripts', 'python.exe');
    this.childProcess = spawn(pythonExecutable, [this.script_path, port, baudRate.toString()]);

    this.childProcess.stdout.on('data', (chunk: Buffer) => this.handleData(chunk));
    this.childProcess.stderr.on('data', (data: Buffer) => {
      console.error(`Python script error: ${data.toString()}`);
      if (this.onError) this.onError(data.toString());
    });
    this.childProcess.on('close', () => this.handleClose());
  }

  disconnect(): void {
    console.log("disconnect ModbusRTU",this.id,this);
    if (this.childProcess) {
      console.log("kill ModbusRTU");
      this.childProcess.kill();
      this.childProcess = null;
    }
  }

  async writeReg(slaveId: number, address: number, value: number): Promise<any> {
    return new Promise((resolve, reject) => {
      const requestId = (this.requestIdCounter++).toString();
      this.pendingRequests.set(requestId, { resolve, reject });
      
      const command = { type: 'write_register', slave_id: slaveId, address, value, requestId };
      this.sendCommand(command);

      setTimeout(() => {
          if (this.pendingRequests.has(requestId)) {
              this.pendingRequests.delete(requestId);
              reject(new Error('Modbus write request timed out.'));
          }
      }, 5000);
    });
  }

  async writeRegs(slaveId: number, address: number, values: number[]): Promise<any> {
    return new Promise((resolve, reject) => {
      const requestId = (this.requestIdCounter++).toString();
      this.pendingRequests.set(requestId, { resolve, reject });
      
      const command = { type: 'write_registers', slave_id: slaveId, address, values, requestId };
      this.sendCommand(command);

      setTimeout(() => {
          if (this.pendingRequests.has(requestId)) {
              this.pendingRequests.delete(requestId);
              reject(new Error('Modbus write multiple request timed out.'));
          }
      }, 5000);
    });
  }

  async readRegs(slaveId: number, address: number, count: number): Promise<number[]> {
    return new Promise((resolve, reject) => {
      const requestId = (this.requestIdCounter++).toString();
      this.pendingRequests.set(requestId, { resolve, reject });
      
      const command = { type: 'read_holding_registers', slave_id: slaveId, address, count, requestId };
      this.sendCommand(command);

      setTimeout(() => {
          if (this.pendingRequests.has(requestId)) {
              this.pendingRequests.delete(requestId);
              reject(new Error('Modbus read request timed out.'));
          }
      }, 5000); // 5 second timeout
    });
  }

  private sendCommand(command: object): void {
    if (this.childProcess && this._isOpen) {
      const commandString = JSON.stringify(command);
      this.childProcess.stdin.write(commandString + '\n', (err: Error | null) => {
        if (err) {
          console.error('Error writing to child process stdin:', err);
        }
      });
    } else {
      console.warn('Cannot send command: port not open or process not initialized.');
    }
  }

  private handleData(chunk: Buffer): void {
    const messages = chunk.toString().split('\n').filter((msg: string) => msg.trim() !== '');
    messages.forEach((message: string) => {
      try {
        const parsed = JSON.parse(message);
        
        if (parsed.requestId && this.pendingRequests.has(parsed.requestId)) {
          const promise = this.pendingRequests.get(parsed.requestId);
          if (promise) {
            if (parsed.type === 'error') {
              promise.reject(new Error(parsed.payload));
            } else {
              promise.resolve(parsed.payload);
            }
            this.pendingRequests.delete(parsed.requestId);
          }
        } else {
          if (parsed.type === 'data') {
            if (this.onData) this.onData(parsed.payload);
          } else if (parsed.type === 'error') {
            if (this.onError) this.onError(parsed.payload);
          } else if (parsed.type === 'status') {
            if (parsed.payload === 'connected') {
              this._isOpen = true;
              if (this.onConnect) this.onConnect();
            } else if (parsed.payload === 'disconnected') {
              this.handleClose();
            }
          }
        }
      } catch (e) {
        console.warn('Non-JSON message from python script:', message);
      }
    });
  }

  private handleClose(): void {
    if (this._isOpen) {
      this._isOpen = false;
      this.childProcess = null;
      if (this.onDisconnect) this.onDisconnect();
      console.log('Serial connection process terminated.');
    }
  }
}

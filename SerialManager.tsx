import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Button, Select, Input, InputNumber, Space } from 'antd';
import { ModbusRTU } from './lib/ModbusRTU';
const { Option } = Select;
const { TextArea } = Input;

const parseNumber = (s: string): number => {
    const trimmed = s.trim();
    if (trimmed.toLowerCase().startsWith('0x')) {
        return parseInt(trimmed, 16);
    }
    return parseInt(trimmed, 10);
};

const SerialManager: React.FC<{env_path: string, lib_path: string, UI_path: string}> = ({env_path, lib_path, UI_path}) => {
  const modbusClient = useMemo(() => {
    let path = (window as any).require('path');
    let UI_folder = path.dirname(UI_path);
    let script_path = path.join(UI_folder, 'script', 'serial_ctrl.py');
    let venv_path = path.join(UI_folder, 'script', 'venv');
    return new ModbusRTU(script_path, venv_path);
  }, [UI_path]);
  
  const [ports, setPorts] = useState<any[]>([]);
  const [isOpen, setIsOpen] = useState(modbusClient.isOpen);
  const [receivedData, setReceivedData] = useState<string[]>([]);

  useEffect(() => {
    const handleConnect = () => setIsOpen(true);
    const handleDisconnect = () => setIsOpen(false);
    const handleData = (data: string) => {
        setReceivedData(prev => [...prev, `[Unsolicited] ${JSON.stringify(data)}`]);
    };
    const handleError = (error: string) => {
        setReceivedData(prev => [...prev, `[Error] ${error}`]);
    }

    modbusClient.onConnect = handleConnect;
    modbusClient.onDisconnect = handleDisconnect;
    modbusClient.onData = handleData;
    modbusClient.onError = handleError;

    // Initial port list fetch
    modbusClient.listPorts().then(setPorts).catch((err: Error) => console.error(err));

    return () => {
      modbusClient.onConnect = null;
      modbusClient.onDisconnect = null;
      modbusClient.onData = null;
      modbusClient.onError = null;
      modbusClient.disconnect();
    };
  }, [modbusClient]);

  const loadConfig = useCallback((silent = false) => {
    const fs = (window as any).require('fs');
    const path = (window as any).require('path');
    const configFilePath = path.join(env_path, 'ModbusClientSetup.json');

    if (fs.existsSync(configFilePath)) {
        try {
            const data = fs.readFileSync(configFilePath, 'utf8');
            const config = JSON.parse(data);
            if (config.selectedPort) setSelectedPort(config.selectedPort);
            if (config.baudRate) setBaudRate(config.baudRate);
            if (config.savedCommands) setSavedCommands(config.savedCommands);
            if (!silent) alert('Configuration loaded successfully.');
        } catch (err: any) {
            if (!silent) alert(`Failed to load configuration: ${err.message}`);
            console.error('Failed to load or parse configuration file:', err);
        }
    } else {
        if (!silent) alert('No configuration file found.');
    }
  }, [env_path]);

  useEffect(() => {
    loadConfig(true);
  }, [loadConfig]);

  const refreshPorts = async () => {
    try {
        const portList = await modbusClient.listPorts();
        setPorts(portList);
    } catch(err) {
        console.error(err);
    }
  };

  // Connection state
  const [selectedPort, setSelectedPort] = useState<string | null>(null);
  const [baudRate, setBaudRate] = useState<number>(9600);
  
  // Modbus command state
  const [slaveId, setSlaveId] = useState<number>(1);
  const [modbusFunction, setModbusFunction] = useState<string>('write_register');
  const [address, setAddress] = useState<string>('0');
  const [value, setValue] = useState<string>('0'); // Can be a single value or comma-separated for multi-write

  const [functionName, setFunctionName] = useState('');
  const [savedCommands, setSavedCommands] = useState<{name: string, slaveId: number, modbusFunction: string, address: number, value: string}[]>([]);

  const handleConnect = () => {
    if (selectedPort) {
        setReceivedData([]);
        modbusClient.connect(selectedPort, baudRate);
    } else {
        alert("Please select a port.");
    }
  }

  const executeModbusCommand = async (command: { name?: string, slaveId: number, modbusFunction: string, address: number, value: string }) => {
    const { slaveId, modbusFunction, address, value } = command;
    try {
      let result: any;
      const commandName = command.name ? `[${command.name}] ` : '';
      switch (modbusFunction) {
        case 'write_register':
          result = await modbusClient.writeReg(slaveId, address, parseNumber(value));
          setReceivedData((prev: string[]) => [...prev, `${commandName}Write OK: ${JSON.stringify(result)}`]);
          break;
        case 'write_registers':
          const values = value.split(/[,\s]+/).filter(Boolean).map(v => parseNumber(v.trim()));
          result = await modbusClient.writeRegs(slaveId, address, values);
          setReceivedData((prev: string[]) => [...prev, `${commandName}Write OK: ${JSON.stringify(result)}`]);
          break;
        case 'read_holding_registers':
          result = await modbusClient.readRegs(slaveId, address, parseInt(value, 10));
          setReceivedData((prev: string[]) => [...prev, `${commandName}Read OK: [${result.join(', ')}]`]);
          break;
        default:
          console.error("Unknown Modbus function");
          setReceivedData((prev: string[]) => [...prev, `${commandName}Error: Unknown Modbus function`]);
      }
    } catch (e: any) {
        setReceivedData((prev: string[]) => [...prev, `Error: ${e.message}`]);
    }
  };

  const handleSendModbusCommand = async () => {
    const numericAddress = parseNumber(address);
    if (isNaN(numericAddress)) {
        setReceivedData((prev: string[]) => [...prev, `Error: Invalid address format`]);
        return;
    }
    await executeModbusCommand({ slaveId, modbusFunction, address: numericAddress, value });
  };

  const handleAddCommand = () => {
    if (!functionName.trim()) {
        alert("Please enter a function name.");
        return;
    }
    const numericAddress = parseNumber(address);
    if (isNaN(numericAddress)) {
        alert("Invalid address format.");
        return;
    }
    const newCommand = {
        name: functionName,
        slaveId,
        modbusFunction,
        address: numericAddress,
        value
    };
    setSavedCommands(prev => [...prev, newCommand]);
    setFunctionName(''); // Reset input
  };

  const handleRunSavedCommand = async (command: { name?: string, slaveId: number, modbusFunction: string, address: number, value: string }) => {
    setSlaveId(command.slaveId);
    setModbusFunction(command.modbusFunction);
    setAddress(command.address.toString());
    setValue(command.value);
    await executeModbusCommand(command);
  }

  const handleLoad = () => {
    if (isOpen) {
        modbusClient.disconnect();
    }
    loadConfig();
  };

  const handleSave = () => {
    const fs = (window as any).require('fs');
    const path = (window as any).require('path');
    const configFilePath = path.join(env_path, 'ModbusClientSetup.json');
    const config = {
        selectedPort,
        baudRate,
        savedCommands
    };

    fs.writeFile(configFilePath, JSON.stringify(config, null, 2), (err: Error) => {
        if (err) {
            alert(`Failed to save configuration: ${err.message}`);
        } else {
            alert('Configuration saved successfully.');
        }
    });
  };

  const getFunctionInputLabel = () => {
    switch(modbusFunction) {
      case 'write_register': return 'Value';
      case 'write_registers': return 'Values (CSV)';
      case 'read_holding_registers': return 'Count';
      default: return 'Value';
    }
  }

  return (
    <div style={{ padding: '10px' }}>
      <h3>Serial Port Manager</h3>
      <div style={{ marginBottom: '10px' }}>
        <Select
          style={{ width: 200, marginRight: '10px' }}
          placeholder="Select a port"
          onChange={setSelectedPort}
          value={selectedPort}
          disabled={isOpen}
        >
          {ports.map((port: any, index: number) => (
            <Option key={index} value={port.path}>{port.path}</Option>
          ))}
        </Select>
        <Select
            value={baudRate}
            style={{ width: 120, marginRight: '10px' }}
            onChange={setBaudRate}
            disabled={isOpen}
            >
            <Option value={9600}>9600</Option>
            <Option value={19200}>19200</Option>
            <Option value={38400}>38400</Option>
            <Option value={57600}>57600</Option>
            <Option value={115200}>115200</Option>
        </Select>
        <Button onClick={refreshPorts} style={{ marginRight: '10px' }} disabled={isOpen}>Refresh Ports</Button>
        {isOpen ? (
          <Button type="primary" danger onClick={() => modbusClient.disconnect()}>Disconnect</Button>
        ) : (
          <Button type="primary" onClick={handleConnect} disabled={!selectedPort}>Connect</Button>
        )}
        <Button onClick={handleSave} style={{ marginLeft: '10px' }}>Save</Button>
        <Button onClick={handleLoad} style={{ marginLeft: '10px' }}>Load</Button>
        <span style={{ marginLeft: '10px', color: isOpen ? 'green' : 'red' }}>
          {isOpen ? 'Connected' : 'Disconnected'}
        </span>
      </div>
      <div style={{ marginBottom: '10px' }}>
        <h4>Modbus RTU Control</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <Space>
                <InputNumber addonBefore="Slave ID" value={slaveId} onChange={(v) => setSlaveId(v || 1)} />
                <Select value={modbusFunction} onChange={setModbusFunction} style={{ width: 200 }}>
                    <Option value="write_register">WriteReg(06)</Option>
                    <Option value="write_registers">WriteRegs(10)</Option>
                    <Option value="read_holding_registers">ReadRegs(03)</Option>
                </Select>
                <Input addonBefore="Address" value={address} onChange={(e) => setAddress(e.target.value)} />
            </Space>
            <Space align="start">
                {modbusFunction === 'write_registers' ? (
                    <div style={{width: 500}}>
                        <div style={{ padding: '4px 11px', color: 'rgba(0, 0, 0, 0.88)', fontSize: 14, background: '#fafafa', border: '1px solid #d9d9d9', borderRadius: '6px 6px 0 0', borderBottom: 0 }}>{getFunctionInputLabel()}</div>
                        <TextArea
                            value={value}
                            onChange={(e) => setValue(e.target.value)}
                            autoSize={{ minRows: 2, maxRows: 6 }}
                        />
                    </div>
                ) : (
                    <Input addonBefore={getFunctionInputLabel()} value={value} onChange={(e) => setValue(e.target.value)} />
                )}
                <Button onClick={handleSendModbusCommand} disabled={!isOpen}>Send Command</Button>
            </Space>
        </div>
        <div style={{ marginTop: '10px' }}>
            <Space>
                <Input 
                    addonBefore="Function Name" 
                    value={functionName} 
                    onChange={(e) => setFunctionName(e.target.value)} 
                    placeholder="Enter name for quick command"
                    style={{ width: 300 }}
                    disabled={!isOpen}
                />
                <Button onClick={handleAddCommand} disabled={!isOpen || !functionName.trim()}>Add</Button>
            </Space>
        </div>
      </div>
      {savedCommands.length > 0 && (
        <div style={{ marginBottom: '10px' }}>
            <h4>Quick Commands</h4>
            <Space wrap>
                {savedCommands.map((cmd, index) => (
                    <Button key={index} onClick={() => handleRunSavedCommand(cmd)} disabled={!isOpen}>
                        {cmd.name}
                    </Button>
                ))}
            </Space>
        </div>
      )}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
          <h4 style={{ margin: 0 }}>Received Data:</h4>
          <Button onClick={() => setReceivedData([])}>Clear</Button>
        </div>
        <TextArea
            autoSize={{ minRows: 5, maxRows: 15 }}
            value={receivedData.join('\n')}
            readOnly
            style={{ background: '#f0f2f5' }}
        />
      </div>
    </div>
  );
};

export default SerialManager;

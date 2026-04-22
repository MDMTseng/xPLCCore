import sys
import json
import time
from pymodbus.client.sync import ModbusSerialClient # type: ignore

def list_ports():
    """Lists available serial ports and prints them as a JSON array."""
    # This relies on pyserial, which is a dependency of pymodbus
    import serial.tools.list_ports # type: ignore
    ports = serial.tools.list_ports.comports()
    port_list = []
    for port in ports:
        port_list.append({
            "path": port.device,
            "description": port.description,
            "manufacturer": port.manufacturer
        })
    print(json.dumps(port_list))
    sys.stdout.flush()

def main():
    """Main function to handle command-line arguments and Modbus RTU communication."""
    if len(sys.argv) > 1 and sys.argv[1] == 'list_ports':
        list_ports()
        return

    if len(sys.argv) < 3:
        print(json.dumps({"type": "error", "payload": "Usage: python serial_ctrl.py <PORT> <BAUD>"}))
        sys.stdout.flush()
        return

    port = sys.argv[1]
    try:
        baudrate = int(sys.argv[2])
    except ValueError:
        print(json.dumps({"type": "error", "payload": "Baud rate must be an integer."}))
        sys.stdout.flush()
        return

    client = ModbusSerialClient(method='rtu', port=port, baudrate=baudrate, timeout=1,
                                stopbits=1, bytesize=8, parity='N')

    if not client.connect():
        print(json.dumps({"type": "error", "payload": f"Failed to connect on port '{port}'"}))
        sys.stdout.flush()
        return

    print(json.dumps({"type": "status", "payload": "connected"}))
    sys.stdout.flush()

    try:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                slave_id = int(command.get('slave_id', 1))
                address = int(command.get('address', 0))
                request_id = command.get('requestId')
                response = None

                if command['type'] == 'read_holding_registers':
                    count = int(command.get('count', 1))
                    result = client.read_holding_registers(address, count, unit=slave_id)
                    if not result.isError():
                        response = {"type": "data", "payload": result.registers}
                    else:
                        response = {"type": "error", "payload": str(result)}

                elif command['type'] == 'write_register':
                    value = int(command.get('value', 0))
                    result = client.write_register(address, value, unit=slave_id)
                    if not result.isError():
                        response = {"type": "data", "payload": {"address": result.address, "value": result.value}}
                    else:
                        response = {"type": "error", "payload": str(result)}
                
                elif command['type'] == 'write_registers':
                    values = [int(v) for v in command.get('values', [])]
                    result = client.write_registers(address, values, unit=slave_id)
                    if not result.isError():
                        response = {"type": "data", "payload": {"address": result.address, "count": len(values)}}
                    else:
                        response = {"type": "error", "payload": str(result)}

                else:
                    response = {"type": "error", "payload": f"Unknown command type: {command.get('type')}"}

                if response:
                    if request_id:
                        response['requestId'] = request_id
                    print(json.dumps(response))
                    sys.stdout.flush()

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                error_response = {"type": "error", "payload": f"Invalid command: {line.strip()}. Error: {e}"}
                print(json.dumps(error_response))
                sys.stdout.flush()

    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        print(json.dumps({"type": "status", "payload": "disconnected"}))
        sys.stdout.flush()

if __name__ == '__main__':
    main()

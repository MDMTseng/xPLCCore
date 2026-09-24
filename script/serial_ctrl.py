import sys
import json
import time

# Port "MOCK": no serial port and no pymodbus; the feeder's registers live
# in memory (see MockFeederClient). For running the UI in the all-virtual
# scene. pymodbus is imported only for a real port.
MOCK_PORT = "MOCK"
VISION_MOCK_NOTIFY = ("127.0.0.1", 7951)   # tools/sim/vision_mock.py, optional

def list_ports():
    """Lists available serial ports and prints them as a JSON array."""
    # This relies on pyserial, which is a dependency of pymodbus
    import serial.tools.list_ports # type: ignore
    ports = serial.tools.list_ports.comports()
    port_list = [{"path": MOCK_PORT, "description": "Simulated feeder (no hardware)", "manufacturer": "sim"}]
    for port in ports:
        port_list.append({
            "path": port.device,
            "description": port.description,
            "manufacturer": port.manufacturer
        })
    print(json.dumps(port_list))
    sys.stdout.flush()

class _Result:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def isError(self):
        return False


class MockFeederClient:
    """Stands in for pymodbus' ModbusSerialClient with the feeder's register
    map (doc/3-subsystems/flex_feeder.md) held in memory.

    Command space writes (FC06, what the UI uses) are also reported to the
    vision mock over UDP, so a simulated vibration can reshuffle the parts
    the simulated feeder camera sees. Best effort: nothing listens, nothing
    happens."""

    def __init__(self):
        self.regs = {0x0039: 0x0501}   # RS-485 19200 baud, address 1
        import socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def connect(self):
        return True

    def close(self):
        pass

    def _notify(self, address, value):
        msg = json.dumps({"event": "feeder_write", "address": address, "value": value})
        try:
            self._sock.sendto(msg.encode(), VISION_MOCK_NOTIFY)
        except OSError:
            pass

    def read_holding_registers(self, address, count, unit=1):
        return _Result(registers=[self.regs.get(address + i, 0) for i in range(count)])

    def write_register(self, address, value, unit=1):
        self.regs[address] = value
        self._notify(address, value)
        return _Result(address=address, value=value)

    def write_registers(self, address, values, unit=1):
        for i, v in enumerate(values):
            self.regs[address + i] = v
        return _Result(address=address, count=len(values))


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

    if port.upper() == MOCK_PORT:
        client = MockFeederClient()
    else:
        from pymodbus.client.sync import ModbusSerialClient # type: ignore
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

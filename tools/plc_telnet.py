"""Send commands to the PLC's RTOS shell (Kyland Intewell, telnet :23, no
login) and print the answers. Read-only use: help, task, ifconfig,
"ethercat master", cpuuse. Read doc/4-dev/claude_memory/plc-exception-remote-recovery.md
before using tr / ts / td on a task.

    python tools/plc_telnet.py task ifconfig
    python tools/plc_telnet.py --host 192.168.1.70 "ethercat master"
"""
import socket, sys, time
args = sys.argv[1:]
host = "192.168.1.70"
if args[:1] == ["--host"]:
    host, args = args[1], args[2:]
s = socket.create_connection((host, 23), timeout=10)
def rd(t=1.5):
    s.settimeout(t); out = b""
    try:
        while True:
            d = s.recv(65536)
            if not d: break
            out += d
    except Exception: pass
    return out.decode("latin1", "replace")
rd()
for c in args:
    s.sendall((c + "\r\n").encode()); print("$", c); print(rd(2.5))

# -*- coding: ascii -*-
# Inspect the 'Send' method of FB_TcpMsgPakServer -- it's one of the POUs
# that errored after import. Compare first-scan bytes in the project vs on
# disk, to find the encoding divergence.

import os, codecs

proj = projects.primary

def walk(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk(c): yield k
    except Exception: pass

fb = None; send = None
for top in proj.get_children():
    for o in walk(top):
        try:
            if o.get_name() == "FB_TcpMsgPakServer":
                fb = o
                for c in o.get_children():
                    if c.get_name() == "Send":
                        send = c
                        break
                break
        except Exception: pass
    if send: break

print("Send method found:", send is not None)
decl_text = send.textual_declaration.text
print("decl type:", type(decl_text))
print("decl len:", len(decl_text))

# Print first 200 chars with explicit codepoints
first = decl_text[:200]
print("decl[:200] repr:")
print(repr(first))

# First non-ASCII codepoint
for i, ch in enumerate(decl_text):
    if ord(ch) > 127:
        print("first non-ASCII at pos {}: U+{:04X} ({})".format(i, ord(ch), repr(ch)))
        lo = max(0, i - 20); hi = min(len(decl_text), i + 20)
        print("  context:", repr(decl_text[lo:hi]))
        break
else:
    print("no non-ASCII in decl")

# Read the file on disk
disk_path = r"C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_code\Application\COMM_FBs\FB_TcpMsgPakServer\Send.st"
f = open(disk_path, "rb"); disk_bytes = f.read(); f.close()
print("\ndisk bytes len:", len(disk_bytes))
# Decode and find first non-ASCII
disk_unicode = disk_bytes.decode("utf-8", "replace")
for i, ch in enumerate(disk_unicode):
    if ord(ch) > 127:
        print("first non-ASCII on disk at pos {}: U+{:04X}".format(i, ord(ch)))
        lo = max(0, i - 20); hi = min(len(disk_unicode), i + 20)
        print("  context:", repr(disk_unicode[lo:hi]))
        break

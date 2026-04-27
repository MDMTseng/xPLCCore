# -*- coding: ascii -*-
proj = projects.primary
def nm(o):
    try: return o.get_name()
    except: return "?"
def ty(o):
    try: return o.type
    except: return "?"

def walk(o, d=0, mx=8):
    if d > mx: return
    print("{}{} [{}]".format("  "*d, nm(o), ty(o)))
    try:
        for c in o.get_children(): walk(c, d+1, mx)
    except: pass

print("==== Application/SpiderR tree ====")
for top in proj.get_children():
    if nm(top) == "Device":
        for c in top.get_children():
            if nm(c) == "Plc Logic":
                for app in c.get_children():
                    if nm(app) == "Application":
                        for ch in app.get_children():
                            if nm(ch) == "SpiderR":
                                walk(ch, 0, 8)

print("\n==== EtherCAT_Master_SoftMotion tree ====")
for top in proj.get_children():
    if nm(top) == "Device":
        for c in top.get_children():
            if nm(c) == "EtherCAT_Master_SoftMotion":
                walk(c, 0, 6)

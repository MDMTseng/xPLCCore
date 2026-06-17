# -*- coding: ascii -*-
# Walk the Task Configuration and list what POUs each task calls.
proj = projects.primary

def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name: return c
            except Exception: pass
    except Exception: pass
    return None

# Find /Device/Plc Logic/Application/Task Configuration
for top in proj.get_children():
    if top.get_name() == "Device":
        plc = find_child(top, "Plc Logic")
        app = find_child(plc, "Application")
        tc  = find_child(app, "Task Configuration")
        if tc is None:
            print("no Task Configuration")
        else:
            print("Task Configuration children:")
            for t in tc.get_children():
                print("  task:", t.get_name())
                for sub in t.get_children():
                    try:
                        print("    ->", sub.get_name(), type(sub).__name__)
                    except Exception as ex:
                        print("    ->", ex)
                # Try to read POU-call list
                for attr in ["get_pou_call_list", "pou_call_list", "calls"]:
                    if hasattr(t, attr):
                        print("    attr:", attr, getattr(t, attr))
        break

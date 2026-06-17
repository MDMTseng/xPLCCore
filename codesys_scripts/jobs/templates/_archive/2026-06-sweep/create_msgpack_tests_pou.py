# -*- coding: ascii -*-
# Create the FB_MsgPackTests POU skeleton + its two methods so that
# import_all has somewhere to push the .st contents.
# Idempotent: if the POU / methods already exist, reports and exits.

proj = projects.primary
print("project:", proj.path)

def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name: return c
            except Exception:
                pass
    except Exception:
        pass
    return None

def resolve_path(parts):
    root = None
    for top in proj.get_children():
        try:
            if top.get_name() == "Device":
                root = top; break
        except Exception:
            pass
    if root is None: return None
    cur = find_child(root, "Plc Logic")
    if cur is None: return None
    for part in parts:
        nxt = find_child(cur, part)
        if nxt is None: return None
        cur = nxt
    return cur

parent = resolve_path(["Application", "COMM_FBs"])
if parent is None:
    print("ERROR: Application/COMM_FBs not found"); raise SystemExit(1)
print("parent:", parent.get_name())

# --- Probe which creation API is available on this CODESYS build ---
create_attrs = [a for a in dir(parent) if "create" in a.lower() or "add" in a.lower()]
print("parent creation attrs:", create_attrs)

# Try common forms. Order matters -- whichever resolves first wins.
fb = find_child(parent, "FB_MsgPackTests")
if fb is not None:
    print("FB already exists:", fb.get_name())
else:
    tried = []
    # Form 1: ScriptObjectMarker enum on parent (most common in SP21)
    try:
        # typical: parent.create_pou(name, ImplementationLanguages.ST, PouType.FunctionBlock)
        from ScriptEngine import PouType, ImplementationLanguages
        fb = parent.create_pou("FB_MsgPackTests",
                               PouType.FunctionBlock,
                               ImplementationLanguages.ST)
        print("created via create_pou(PouType.FunctionBlock)")
    except Exception as ex:
        tried.append(("create_pou+PouType", str(ex)))
    # Form 2: simpler create_pou
    if fb is None:
        try:
            fb = parent.create_pou("FB_MsgPackTests")
            print("created via bare create_pou")
        except Exception as ex:
            tried.append(("create_pou bare", str(ex)))
    # Form 3: project-level create on specific parent
    if fb is None:
        try:
            fb = proj.create_pou(parent, "FB_MsgPackTests")
            print("created via proj.create_pou")
        except Exception as ex:
            tried.append(("proj.create_pou", str(ex)))
    if fb is None:
        print("Could not create POU. Attempts:")
        for label, err in tried:
            print("  ", label, "->", err)
        print("")
        print("FALLBACK: please create the POU manually in IDE:")
        print("  Application/COMM_FBs -> Add Object -> Function Block -> FB_MsgPackTests")
        print("  Then Add Object -> Method -> RunAll (private)")
        print("  Then Add Object -> Method -> Fail   (private, VAR_INPUT sMsg : STRING(250);)")
        raise SystemExit(1)
    print("FB created:", fb.get_name())

# --- Create methods ---
def ensure_method(owner, name):
    existing = find_child(owner, name)
    if existing is not None:
        print("method already exists:", name); return existing
    tried = []
    m = None
    try:
        from ScriptEngine import ImplementationLanguages
        m = owner.create_method(name, ImplementationLanguages.ST)
    except Exception as ex:
        tried.append(("create_method+lang", str(ex)))
    if m is None:
        try:
            m = owner.create_method(name)
        except Exception as ex:
            tried.append(("create_method bare", str(ex)))
    if m is None:
        print("Could not create method", name, "tried:", tried)
        return None
    print("method created:", name)
    return m

m_runall = ensure_method(fb, "RunAll")
m_fail   = ensure_method(fb, "Fail")

print("")
print("done. Next step: run `import_all` to push .st text into these skeletons.")

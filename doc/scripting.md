# CODESYS scripting infrastructure

Tooling to drive a running CODESYS 3.5 IDE from the command line, and to
round-trip all textual POU content between the open project and a
version-controlled `codesys_code/` tree on disk.

Target env: **CODESYS 3.5 SP21 Patch 20**, IronPython 2.7 scripting
engine, Windows.

## Quick start after a context reset

1. CODESYS IDE must be open with `C:\Users\X1\Desktop\XPack2_codesys\PackerX.project` loaded.
2. In IDE: **Tools → Scripting → Execute Script File… → `codesys_scripts/tcp_server.py`**.
   Leave it running. It listens on `127.0.0.1:9790`.
3. From Windows shell / bash:
   ```
   python codesys_scripts/tcp_client.py <template-name>
   ```
   where `<template-name>` is any `.py` in `codesys_scripts/jobs/templates/`
   (without extension). The template runs **inside CODESYS** against the
   open project; stdout streams back; exit code follows `__DONE__ rc=<n>`.

Cold start (launching CODESYS + opening project from CLI via `build.py`)
takes 30–60s. Warm session is <200ms per call — always prefer the server.

## Architecture

```
 your shell                          CODESYS IDE
 ┌─────────────────┐   TCP 9790   ┌──────────────────────┐
 │ tcp_client.py   │ ───body──►   │ tcp_server.py        │
 │  (Python 3)     │              │  (IronPython 2.7)    │
 │                 │ ◄─stdout──   │  exec() in           │
 │ sends template  │              │  projects.primary    │
 │ reads stdout    │ ◄─rc=N───    │  scope               │
 └─────────────────┘              └──────────────────────┘
```

Protocol: client sends the script body then `\n__EOF__\n`. Server
`exec()`s it, captures stdout/stderr, and streams lines back followed by
`__DONE__ rc=0` (or `rc=1` on exception). Client breaks on `__DONE__`
and closes.

`sys.settrace(None)` and `system.trace = False` run at script start to
suppress line-level tracing — otherwise every executed line floods the
CODESYS Script Messages pane and wrecks throughput.

Server stashes the listening socket in `system._tcp_srv_socket` so the
next `Execute Script File` of `tcp_server.py` can close the old one
before binding again.

## Key files

| File | Purpose |
|---|---|
| `tcp_server.py` | Long-running TCP listener inside CODESYS. Start via IDE menu. |
| `tcp_client.py` | Python 3 client. Sends a template and prints output. |
| `jobs/templates/export_all.py` | Walk project, write every textual object to `codesys_code/`. |
| `jobs/templates/import_all.py` | Walk `codesys_code/`, push text back into project, then `generate_code()`. |
| `build.py` | Cold-start builder: launches CODESYS, opens project, runs script, captures messages. Use only when no warm session is available. |
| `watcher.py` | Legacy file-drop job watcher (pre-TCP). Not typically needed. |

## Round-trip conventions

`codesys_code/` layout mirrors the CODESYS object tree, with `Device`
and `Plc Logic` flattened away:

```
codesys_code/
  Application/
    APPs/
      AxisGroupSM.st          <- single-file POU (no children)
    Robot_FBs/
      AxisGroupManager/       <- POU with methods/actions/properties
        AxisGroupManager.st   <- its own decl+impl
        FB_init.st            <- method
        Transition.st         <- action
        Update.st             <- method
```

A single `.st` file holds both `textual_declaration` and
`textual_implementation` separated by exactly this marker line:

```
(* =========== IMPLEMENTATION =========== *)
```

Declaration-only objects (DUT, GVL, struct) have no marker.

Skipped subtrees: `Library Manager`, `Task Configuration`,
`Symbol Configuration`, `Trace`.

### Encoding — do not get this wrong

IronPython 2.7's `str` vs `unicode` type is **not reliable** via
`isinstance`. `textual_declaration.text` returns `str` for ASCII-only
content but may hold non-ASCII codepoints that silently latin-1-encode
if you let `open(path, 'w').write(str)` do its default thing. This
corrupts UTF-8 (e.g. `Â` appears before non-breaking spaces).

**Rule:** in export, always `.encode("utf-8", "replace")` before
writing bytes. In import, always read bytes and `.decode("utf-8",
"replace")`. Both sides of the export/import pair already do this —
don't "simplify" them.

### Editing POU text from a script

`textual_declaration.text` and `textual_implementation.text` are
**read-only properties**. To modify, use position-based replace:

```python
td = obj.textual_declaration
td.replace(0, td.length, new_text)   # full overwrite
```

`replace(offset, length, text)` and `insert(offset, text)` are the only
mutators. Assigning `td.text = ...` silently fails (or raises in some
builds).

### Idempotency test

After any edit via template, round-trip should be a no-op:

```
python codesys_scripts/tcp_client.py export_all
python codesys_scripts/tcp_client.py import_all   # expect applied=0
```

If `applied > 0` on a second run, the export/import split logic or
whitespace handling is drifting — debug with
`jobs/templates/diag_roundtrip.py`.

## Writing a new template

1. Drop `my_thing.py` into `jobs/templates/`.
2. Start with `# -*- coding: ascii -*-` (avoid em-dashes, curly quotes,
   NBSP in Python source — IronPython's parser chokes).
3. Globals available inside the `exec`: `projects`, `system`, `online`,
   plus standard IronPython + .NET. `projects.primary` is the open
   project.
4. Run with `python codesys_scripts/tcp_client.py my_thing`.

### Common API patterns

Walk everything:
```python
def walk(o):
    yield o
    try:
        for c in o.get_children():
            for k in walk(c): yield k
    except Exception: pass

for top in projects.primary.get_children():
    for o in walk(top): ...
```

Find named child:
```python
def find_child(obj, name):
    try:
        for c in obj.get_children():
            try:
                if c.get_name() == name: return c
            except Exception: pass
    except Exception: pass
    return None
```

Filter to objects with source (avoid library refs / configs):
```python
def has_src(o):
    td = getattr(o, "textual_declaration", None)
    return td is not None and getattr(td, "text", None) is not None
```

Trigger build and collect diagnostics (the message-store API is
undocumented — this is the working form):
```python
for app in list(projects.primary.find("Application", True) or []):
    app.generate_code()

for cat in system.get_message_categories():
    desc = system.get_message_category_description(cat)
    if "build" not in desc.lower(): continue
    for m in system.get_message_objects(cat):
        sev = str(getattr(m, "severity", ""))
        txt = getattr(m, "text", None) or str(m)
        pos = getattr(m, "position_text", "") or ""
        # "error" / "warning" appear in sev
```

Online (PLC must be in run mode — returns strings like `"LREAL#3.14"`
or `"SMC_AXIS_STATE.standstill"`):
```python
oapp = online.create_online_application(app_obj)
oapp.login(OnlineChangeOption.Try, False)
val = oapp.read_value("GVL.someVar")        # string result
oapp.logout()
```
No `"Application."` prefix in the expression. See
`~/.claude/.../memory/codesys_scripting_online_read.md` for axis field
names and known gotchas.

## Editing workflow

Two viable patterns:

**A. Edit on disk + import** (preferred for larger / reviewable changes)
1. Edit files under `codesys_code/` in your editor.
2. `python codesys_scripts/tcp_client.py import_all` — pushes disk →
   project and runs `generate_code()`. Prints build errors/warnings.
3. Review in IDE, **File → Save** if happy. Import does **not** save.

**B. Scripted surgical edit** (for programmatic refactors)
1. Write a template that locates the object, mutates
   `textual_declaration` / `textual_implementation` via `.replace()`.
2. Run it, check the build output in the template's own
   `generate_code` block.
3. If happy, export to disk so the change is captured there too.

Preview-before-apply is strongly preferred for B: compute `new_text`,
print a diff or the replaced region, stop, then apply in a follow-up
template once the user confirms. (This is a recorded preference.)

## Project not saved by scripts

Neither `import_all` nor any edit template calls `project.save()`.
Rationale: the human should review in the IDE and Ctrl-S intentionally.
If you need to automate save, call `projects.primary.save()` explicitly
at the end of a template.

## Known quirks / failure modes

- **`sys.argv` empty under `--scriptargs`** in IDE script menu — use
  hardcoded paths or read from a sentinel file instead.
- **Non-ASCII in Python source** — IronPython 2.7 doesn't honor PEP 263
  in all contexts. Keep templates pure ASCII. Use `chr(0xE2) + ...` if
  you truly need bytes.
- **`td.text = ...` read-only** — use `td.replace(0, td.length, new)`.
- **Walker matching wrong object by name** — always filter on "has
  textual_declaration with .text" or you'll match a folder named the
  same as a POU.
- **Line tracing floods Script Messages** — already handled in
  `tcp_server.py` via `sys.settrace(None)`, but if you ever bypass the
  server and run a template directly, add that line to the top.
- **Client hangs after `__DONE__`** — fixed: server does
  `conn.shutdown(SHUT_WR)` and client breaks on the marker rather than
  waiting for EOF.
- **`__DONE__` never seen → rc=1** — means server died mid-exec. Check
  the Script Messages pane in CODESYS for a Python traceback.

## Workflow caveats (not obvious until they bite you)

- **Import doesn't create or delete objects.** `import_all` only updates
  the text of objects that already exist in the project tree. If you
  add a new POU on disk, import silently reports it as `[miss]` —
  create the object in the IDE first, then export to capture its
  skeleton, then edit on disk.
- **Rename/delete on the IDE side leaves orphans on disk.** Export
  writes new files but never removes old ones. After a rename or
  delete in the IDE, `git status` in `codesys_code/` will show the
  dead files — delete them manually. Similarly the disk tree can get
  stale if you delete a folder-backed POU's children.
- **Import runs `generate_code()` but does NOT save the project.**
  Always review in the IDE and `File → Save` to persist. If CODESYS
  is closed without save, everything import did is discarded.
- **`projects.primary` picks the currently active project.** If more
  than one is open, be explicit (`projects.all`, filter by path).
- **TCP server is single-connection, single-threaded.** A long-running
  or hung template blocks everything else until it returns. No
  timeout — if a template hangs, kill the script in the IDE (Scripting
  → Stop) and re-run `tcp_server.py`.
- **Each TCP call runs in a fresh globals dict.** State does not
  persist between calls. If you need persistence across calls, stash
  on `system._your_key` (same trick the server uses for its socket).
- **Running a template directly (not via TCP) dumps output to the
  Script Messages pane**, not stdout. Easy to miss errors. Always go
  through `tcp_client.py` unless you're bootstrapping the server
  itself.
- **`generate_code()` lives on the Application object, not the
  project.** `projects.primary.generate_code()` does not exist. Iterate
  `projects.primary.find("Application", True)` and call it on each.
- **Build message category ID is a GUID.** To clear before a build:
  `system.clear_messages("{97f48d64-a2a3-4856-b640-75c046e37ea9}")`.
  That GUID is the build category; other categories (online change,
  etc.) have their own GUIDs — filter by description containing
  `"build"` rather than hardcoding.
- **Object names with filesystem-hostile chars get sanitized**
  (`<>:"/\|?*` → `_`). Reverse lookup in import uses the sanitized
  path, so as long as you don't rename, round-trip is stable. A POU
  literally named `Foo/Bar` would collide with one named `Foo_Bar`.
- **`.project` file is locked by the IDE.** Scripts that try to copy
  or move it while CODESYS is open will fail. Close the IDE first.
- **Online API requires the PLC to be in run mode and the
  Application logged in.** `oapp.login(OnlineChangeOption.Try, False)`
  before `read_value`, `oapp.logout()` after. `Try` is safer than
  `Force` — avoid `Force` unless you understand the online-change
  consequences.
- **IronPython 2.7 syntax gotchas beyond ASCII:**
  - `print` is a statement, not a function. `print("a",  "b")` prints a
    tuple. Use `print "a", "b"` or import from `__future__`.
  - String formatting: `"{}".format(x)` works; f-strings do **not**.
  - `True`/`False` are keywords as expected, but `None`-comparisons
    should use `is None` not `== None` (some CODESYS objects override
    `__eq__` oddly).
- **`build.py` (cold start) expects CODESYS at the default install
  path.** If CODESYS is installed elsewhere, edit the path in
  `build.py`. Prefer the warm TCP flow instead — cold start is 30–60s
  per call.
- **Round-trip preserves whitespace but NOT `.project` object
  metadata** (IDs, GUIDs, position in tree, folder color, etc.). Don't
  expect `codesys_code/` to be a complete project snapshot — it's
  source text only. The authoritative project file is still
  `PackerX.project`.

## Project path

Currently hardcoded in several places:

```
C:\Users\X1\Desktop\XPack2_codesys\PackerX.project
```

`export_all.py` / `import_all.py` write to / read from:

```
C:\Users\X1\Desktop\X2.5\TCP_UI\TCP_UI\codesys_code
```

If either moves, grep for the literal strings — there's no central
config yet.

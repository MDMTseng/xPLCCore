"""Regenerate the script indexes from each script's own header:
codesys_scripts/jobs/templates/README.md and tools/README.md.

    python tools/gen_script_index.py

Each row: the script, what it touches, and the first sentence of its
header comment / docstring. Classification is by what the code does, so it
stays honest when a header does not say:
  jobs:  read-only (--readonly usage or READ-ONLY header), edits the project
         (.save() / parameter or object writes), talks to the PLC (login,
         download, online device), restarts / resets the PLC.
  tools: moves real axes (JOINT_MOVE / G1 / ReelGo, or needs --owner-ok),
         downloads (safe_install / install), writes PLC variables, read-only.
Run it after adding, renaming or archiving a script.
"""

import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = os.path.join(REPO, "codesys_scripts", "jobs", "templates")
TOOLS = os.path.join(REPO, "tools")


def header(text):
    """First sentence of the leading docstring or comment block (line
    scan; no backtracking regex)."""
    lines = text.splitlines()
    k = 0
    while k < len(lines) and (lines[k].strip() == "" or lines[k].strip().startswith("# -*-")):
        k += 1
    body = []
    if k < len(lines) and lines[k].lstrip().startswith('"""'):
        first = lines[k].strip()[3:]
        if '"""' in first:
            body.append(first.split('"""')[0])
        else:
            body.append(first)
            k += 1
            while k < len(lines) and '"""' not in lines[k]:
                body.append(lines[k].strip())
                k += 1
            if k < len(lines):
                body.append(lines[k].split('"""')[0].strip())
    else:
        while k < len(lines) and lines[k].strip().startswith("#"):
            body.append(lines[k].strip().lstrip("#").strip())
            k += 1
    text1 = " ".join(x for x in body if x)
    text1 = " ".join(text1.split())
    if text1.upper().startswith("READ-ONLY."):
        text1 = text1[len("READ-ONLY."):].strip()
    m = re.search(r"[.!?](\s|$)", text1)
    if m:
        text1 = text1[:m.end()].strip()
    return text1[:220].replace("|", "/")


def job_kind(text):
    kinds = []
    ro = "--readonly" in text or "READ-ONLY" in text
    if ro:
        kinds.append("read-only")
    code = re.sub(r"create_online_\w+", "", text)
    if re.search(r"\.save\(\)|\.value\s*=|prm\[0\]\.value|\.enable\(\)|\.disable\(\)|\.remove\(|\.add\(|\.create_|import_", code) and not ro:
        kinds.append("edits project")
    if re.search(r"\.login\(|create_online_application|download|\.start\(\)|\.stop\(\)", text):
        kinds.append("PLC session")
    if re.search(r"reset\(ResetOption|reset_origin|\.reset\(", text):
        kinds.append("resets PLC")
    return ", ".join(kinds) or "?"


LIBRARIES = {"machine.py": "shared library (machine access, safety checks)",
             "plc_direct.py": "library (direct msgpack client, port 8125)",
             "gen_script_index.py": "writes these README files"}


def tool_kind(text, name=""):
    if name in LIBRARIES:
        return LIBRARIES[name]
    kinds = []
    if re.search(r"owner_ok|require_owner_ok|JOINT_MOVE|\"G1\"|cmd=\"G1\"|ReelGo|G1\(", text):
        kinds.append("moves axes")
    if re.search(r"safe_install|\"install\"|install --on-site", text):
        kinds.append("downloads")
    if re.search(r"rpc_write|\"write\"", text):
        kinds.append("writes PLC vars")
    return ", ".join(kinds) or "read-only"


def table(dirpath, kindfn, skip=(), with_name=False):
    rows = []
    for name in sorted(os.listdir(dirpath)):
        if not name.endswith(".py") or name in skip:
            continue
        text = open(os.path.join(dirpath, name), encoding="utf-8", errors="replace").read()
        kind = kindfn(text, name) if with_name else kindfn(text)
        rows.append("| `%s` | %s | %s |" % (name, kind, header(text)))
    return rows


def main():
    jobs = table(JOBS, job_kind)
    with open(os.path.join(JOBS, "README.md"), "w", encoding="utf-8") as f:
        f.write("""# CODESYS job templates

Scripts run inside the CODESYS IDE by the daemon:
`python codesys_scripts/rpc.py exec [--readonly] --file jobs/templates/<name>.py`
(add `PYTHONIOENCODING=utf-8` when the output may hold non-ASCII).

- **read-only**: inspects the project or the PLC, changes nothing.
- **edits project**: changes and saves the project offline. The PLC keeps
  the old code until a deploy (`rpc.py push` for code, `rpc.py install
  --on-site` for device / task configuration). Deploy with the delta
  powered off (`tools/machine.py safe_install`).
- **PLC session**: logs in, downloads, starts or stops.
- **resets PLC**: resets it.

One-off probes live in `_archive/`. Regenerate this file with
`python tools/gen_script_index.py`.

| Job | Touches | What it does |
|---|---|---|
""")
        f.write("\n".join(jobs) + "\n")
    tools = table(TOOLS, tool_kind, with_name=True)
    with open(os.path.join(TOOLS, "README.md"), "w", encoding="utf-8") as f:
        f.write("""# Tools

Host-side scripts. Most talk to the PLC through the standalone UI's
harness link (start the UI with `XPLC_HARNESS=1 node standalone/run.cjs`)
and share `machine.py`:
- the link, with a reconnect;
- the FSM;
- the delta's real / virtual mode;
- the safety checks:
  - `--owner-ok` before the real delta moves;
  - the delta powered off before a download (`safe_install`);
  - SyncOffset <= 50;
  - save the PLC log before retrying.

- **moves axes**: needs the owner's OK when the delta is real.
- **downloads**: goes through `machine.safe_install`.
- **writes PLC vars**: through the CODESYS daemon.

Regenerate this file with `python tools/gen_script_index.py`.

| Tool | Touches | What it does |
|---|---|---|
""")
        f.write("\n".join(tools) + "\n")
    print("jobs: %d, tools: %d" % (len(jobs), len(tools)))


if __name__ == "__main__":
    main()

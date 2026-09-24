"""Refuse an online change when the edit changes the memory layout.

`rpc.py push` logs in with OnlineChangeOption.Try. When CODESYS cannot
online-change an edit it falls back to a full download of the running
application, and on 2026-09-25 that fallback wedged the PLC so badly it
needed a power cycle at the machine (see plc_guard.py). CODESYS cannot
tell a script beforehand whether an online change is possible, so this
module makes a conservative guess from the .st sources instead.

It compares the layout-sensitive declarations on disk against a baseline
recorded at the last successful deploy:

  - VAR CONSTANT blocks of PROGRAMs, FUNCTION_BLOCKs and GVLs (constants
    size arrays: FlyEventBufferSize is what bit us)
  - RETAIN / PERSISTENT blocks
  - any declaration line holding an ARRAY
  - whole TYPE files (structs, enums), including added or removed ones

METHOD and FUNCTION files are skipped: their VARs live on the stack.
Plain variables added to a PROGRAM or FB are also allowed; online change
relocates those.

The baseline lives in the daemon state dir (deployed_layout.json).
Bootstrap or repair it from a git revision known to match the PLC:

    python codesys_scripts/rpc.py layout --baseline-from-git <rev>
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CODE_DIR = "codesys_code"

_BLOCK_START = re.compile(r"^(VAR\w*)\b(.*)$")
_POU_KIND = re.compile(
    r"^\s*(PROGRAM|FUNCTION_BLOCK|FUNCTION|METHOD|PROPERTY|INTERFACE|TYPE|"
    r"VAR_GLOBAL)\b", re.M)


def _strip_comments(text: str) -> str:
    text = re.sub(r"\(\*.*?\*\)", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _norm(line: str) -> str:
    return " ".join(line.split())


def kind_of(text: str) -> str:
    m = _POU_KIND.search(_strip_comments(text))
    return m.group(1) if m else "?"


def signature(text: str) -> list[str]:
    """The layout-sensitive declaration lines of one .st file, normalised
    and tagged with the block they sit in."""
    text = _strip_comments(text)
    kind = kind_of(text)
    if kind in ("METHOD", "FUNCTION", "PROPERTY", "INTERFACE", "?"):
        return []
    lines = [_norm(l) for l in text.splitlines()]
    lines = [l for l in lines if l]
    if kind == "TYPE":
        return ["TYPE: " + l for l in lines]

    out: list[str] = []
    block = None
    for line in lines:
        upper = line.upper()
        if block is None:
            m = _BLOCK_START.match(upper)
            if m:
                block = _norm(upper)
            continue
        if upper.startswith("END_VAR"):
            block = None
            continue
        sensitive = ("CONSTANT" in block or "RETAIN" in block
                     or "PERSISTENT" in block or "ARRAY" in upper)
        if sensitive:
            out.append("%s: %s" % (block, line))
    return out


def scan_tree(read_file, paths) -> dict:
    """{relative path: {"kind": ..., "sig": [...]}} for every .st file."""
    result = {}
    for rel in paths:
        text = read_file(rel)
        result[rel] = {"kind": kind_of(text), "sig": signature(text)}
    return result


def scan_disk(repo: str = REPO) -> dict:
    root = os.path.join(repo, CODE_DIR)
    paths = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".st"):
                full = os.path.join(dirpath, f)
                paths.append(os.path.relpath(full, repo).replace("\\", "/"))

    def read(rel):
        with open(os.path.join(repo, rel), encoding="utf-8",
                  errors="replace") as fh:
            return fh.read()
    return scan_tree(read, sorted(paths))


def scan_git(rev: str, repo: str = REPO) -> dict:
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True).stdout.decode(
                                  "utf-8", "replace")
    paths = [p for p in git("ls-tree", "-r", "--name-only", rev, "--",
                            CODE_DIR).splitlines() if p.endswith(".st")]
    return scan_tree(lambda rel: git("show", "%s:%s" % (rev, rel)),
                     sorted(paths))


def compare(base: dict, cur: dict) -> list[str]:
    """Human-readable layout differences; empty means online change is
    expected to work."""
    problems = []
    for rel in sorted(set(base) | set(cur)):
        b, c = base.get(rel), cur.get(rel)
        if b is None:
            if c["kind"] == "TYPE":
                problems.append("%s: new TYPE" % rel)
            elif c["sig"]:
                problems.append("%s: new file with layout declarations"
                                % rel)
            continue
        if c is None:
            if b["kind"] == "TYPE" or b["sig"]:
                problems.append("%s: removed" % rel)
            continue
        if b["sig"] != c["sig"]:
            gone = [l for l in b["sig"] if l not in c["sig"]]
            new = [l for l in c["sig"] if l not in b["sig"]]
            detail = "; ".join(["- " + l for l in gone[:3]]
                               + ["+ " + l for l in new[:3]])
            problems.append("%s: %s" % (rel, detail or "declarations reordered"))
    return problems


# ---- baseline --------------------------------------------------------

def baseline_path() -> str:
    import config
    return os.path.join(config.state_dir(), "deployed_layout.json")


def load_baseline():
    try:
        with open(baseline_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None


def save_baseline(files: dict, how: str) -> str:
    path = baseline_path()
    data = {"recorded": time.strftime("%Y-%m-%d %H:%M:%S"), "how": how,
            "files": files}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)
    return path


def check_disk() -> tuple[bool, list[str]]:
    """(ok, messages). No baseline counts as not ok: we cannot tell."""
    base = load_baseline()
    if base is None:
        return False, [
            "no deployed layout baseline at %s" % baseline_path(),
            "record one from the git revision the PLC runs:",
            "  rpc.py layout --baseline-from-git <rev>"]
    problems = compare(base["files"], scan_disk())
    return not problems, problems

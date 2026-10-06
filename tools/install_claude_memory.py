"""Copy the Claude Code memory kept in the repo (doc/4-dev/claude_memory/)
into this PC's Claude Code project memory, so a session started in the
workspace folder knows the owner's rules and the machine's history.

Claude Code keeps memory per working directory, under
~/.claude/projects/<slug>/memory/, where <slug> is the working directory with
every character that is not a letter or digit replaced by '-' (e.g.
C:\\Users\\PC\\Documents\\workspace\\codesys_dev -> C--Users-PC-Documents-workspace-codesys-dev).

    python tools/install_claude_memory.py                 # workspace = the repo's parent folder
    python tools/install_claude_memory.py --workdir D:\\dev\\codesys_dev
    python tools/install_claude_memory.py --export        # repo <- this PC (refresh the copy in the repo)

Existing files are only overwritten with --force (import) and are listed
first. Nothing else under ~/.claude is touched.
"""

import argparse
import os
import re
import shutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "doc", "4-dev", "claude_memory")


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", default=os.path.dirname(REPO),
                    help="the folder Claude Code is started in (default: the repo's parent)")
    ap.add_argument("--export", action="store_true", help="copy this PC's memory into the repo instead")
    ap.add_argument("--force", action="store_true", help="overwrite existing memory files")
    a = ap.parse_args()
    dst = os.path.join(os.path.expanduser("~"), ".claude", "projects", slug(a.workdir), "memory")
    if a.export:
        src, out = dst, SRC
    else:
        src, out = SRC, dst
    if not os.path.isdir(src):
        raise SystemExit("nothing to copy: %s does not exist" % src)
    os.makedirs(out, exist_ok=True)
    files = sorted(f for f in os.listdir(src) if f.endswith(".md"))
    clash = [f for f in files if os.path.exists(os.path.join(out, f))]
    if clash and not (a.force or a.export):
        raise SystemExit("already there (use --force to overwrite): %s" % ", ".join(clash))
    for f in files:
        shutil.copy2(os.path.join(src, f), os.path.join(out, f))
    print("%d files: %s -> %s" % (len(files), src, out))


if __name__ == "__main__":
    main()

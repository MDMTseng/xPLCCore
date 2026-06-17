"""Stamp BUILD_GIT_SHA / BUILD_TS_MS in GVL.st with the current git rev
and a unix-ms timestamp. Run before import_all so the running PLC can
answer SYS/VERSION with provenance that matches what's on disk.

Side effects: rewrites two lines of GVL.st in-place. The diff is meant
to land as part of the build/push commit, not be reverted afterwards --
that way an externally-observed BUILD_GIT_SHA is genuinely the sha
that's compiled into the running binary, not just whatever was on disk
at push time.

Idempotent: re-running with the same git state produces the same lines."""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
GVL_PATH = REPO_ROOT / "codesys_code" / "Application" / "GVL.st"


def _git(*args):
    return subprocess.check_output(
        ["git", *args], cwd=str(REPO_ROOT), text=True
    ).strip()


def _resolve_sha():
    try:
        sha = _git("rev-parse", "--short=12", "HEAD")
        # Dirty marker: any unstaged tracked change or staged change. Untracked
        # files don't count (would falsely flag e.g. ad-hoc test logs).
        dirty = subprocess.call(
            ["git", "diff", "--quiet", "HEAD", "--"], cwd=str(REPO_ROOT)
        )
        if dirty != 0:
            sha = f"{sha}-dirty"
        return sha
    except Exception as ex:
        return f"unknown ({type(ex).__name__})"


def main():
    revert = "--revert" in sys.argv[1:]
    if not GVL_PATH.exists():
        print(f"GVL.st not found at {GVL_PATH}", file=sys.stderr)
        return 1

    if revert:
        sha = "unknown"
        ts_ms = 0
    else:
        sha = _resolve_sha()
        ts_ms = int(time.time() * 1000)

    text = GVL_PATH.read_text(encoding="utf-8")
    new_sha_line = f"\tBUILD_GIT_SHA : STRING(40) := '{sha}';"
    new_ts_line = f"\tBUILD_TS_MS   : LINT       := {ts_ms};"

    text2 = re.sub(
        r"\tBUILD_GIT_SHA\s*:\s*STRING\(40\)\s*:=\s*'[^']*'\s*;",
        new_sha_line,
        text,
        count=1,
    )
    text2 = re.sub(
        r"\tBUILD_TS_MS\s*:\s*LINT\s*:=\s*-?\d+\s*;",
        new_ts_line,
        text2,
        count=1,
    )

    if text2 == text:
        print(
            "no BUILD_GIT_SHA / BUILD_TS_MS lines found -- "
            "GVL.st missing the build-provenance VARs",
            file=sys.stderr,
        )
        return 2

    GVL_PATH.write_text(text2, encoding="utf-8")
    verb = "reverted" if revert else "stamped"
    print(f"{verb} GVL.st: sha={sha} ts_ms={ts_ms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

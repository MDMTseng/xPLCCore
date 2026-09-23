# -*- coding: utf-8 -*-
r"""Shared configuration for the CODESYS control scripts.

Imported by BOTH sides, so it must stay IronPython 2.7 compatible
(daemon.py runs inside the CODESYS Scripting Console) and CPython 3
compatible (rpc.py / supervisor.py run in a normal terminal).

Resolution order for the config file:
  1. $XPLC_CONFIG                     -- explicit override
  2. <this file's dir>/codesys_env.json
  3. ./codesys_env.json               -- cwd fallback

Rationale: v1 hardcoded five absolute paths across daemon.py and
build.sh, all pointing at one developer's desktop. Moving the repo to
another machine silently created a junk C:\Users\X1\... tree instead
of failing, because daemon.py called os.makedirs() on a path it could
not possibly own. Everything machine-specific now lives in one JSON
file and a missing key is a hard error.
"""

import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONFIG_NAME = "codesys_env.json"


class ConfigError(Exception):
    pass


def _candidates():
    env = os.environ.get("XPLC_CONFIG")
    if env:
        yield env
    yield os.path.join(_HERE, _CONFIG_NAME)
    yield os.path.join(os.getcwd(), _CONFIG_NAME)


def config_path():
    for path in _candidates():
        if path and os.path.isfile(path):
            return path
    raise ConfigError(
        "no %s found. Looked in: %s. Copy %s.sample and edit it, or set "
        "XPLC_CONFIG to an explicit path."
        % (_CONFIG_NAME, ", ".join([p for p in _candidates() if p]), _CONFIG_NAME)
    )


_cache = {}


def load(reload=False):
    if _cache and not reload:
        return _cache
    path = config_path()
    f = open(path, "rb")
    try:
        raw = f.read()
    finally:
        f.close()
    try:
        text = raw.decode("utf-8")
    except AttributeError:
        text = raw
    data = json.loads(text)
    data["_config_path"] = path
    _cache.clear()
    _cache.update(data)
    return _cache


def get(key, default=None, required=False):
    cfg = load()
    if key in cfg:
        value = cfg[key]
        if str(value).startswith("<SET-ME"):
            raise ConfigError(
                "key %r in %s is still a placeholder: %s"
                % (key, cfg.get("_config_path"), value))
        return value
    if required:
        raise ConfigError(
            "missing required key %r in %s" % (key, cfg.get("_config_path")))
    return default


# ---- typed accessors -------------------------------------------------
# Every path is returned as an absolute native path. Relative values in
# the JSON resolve against the repo root (the parent of this file's dir)
# so the config stays portable between checkouts.

REPO_ROOT = os.path.dirname(_HERE)


def _abspath(value):
    if not value:
        return value
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(REPO_ROOT, value))


def project_path():
    """The .project file the daemon opens and keeps current."""
    return _abspath(get("project", required=True))


def source_root():
    """Directory holding the .st tree (import_all / export_all)."""
    return _abspath(get("source_root", default="codesys_code"))


def state_dir():
    """Where daemon.status, daemon.rpc.log and snapshots live.

    Kept OUT of source_root so heartbeat churn never shows up in a
    git status on the ST tree.
    """
    return _abspath(get("state_dir", default="codesys_scripts/jobs"))


def snapshot_dir():
    return os.path.join(state_dir(), "snapshots")


def codesys_exe():
    return _abspath(get("codesys_exe", required=True))


def codesys_culture():
    """UI/message language for CODESYS launches, e.g. "en-US".

    Worth pinning rather than inheriting the OS locale: message category
    descriptions and build output are localised, so a Chinese-locale
    machine emits Chinese diagnostics that are harder to read, harder to
    grep, and not comparable against a baseline captured elsewhere.
    Empty string means "use the OS language".
    """
    return get("culture", default="en-US")


def codesys_profile():
    return get("profile", required=True)


def rpc_host():
    return get("rpc_host", default="127.0.0.1")


def rpc_port():
    return int(get("rpc_port", default=7420))


def plc_host():
    return get("plc_host", required=True)


def plc_port():
    return int(get("plc_port", default=8125))


def snapshot_keep():
    """How many .project snapshots to retain before pruning oldest."""
    return int(get("snapshot_keep", default=40))


def session_max_jobs():
    """Recycle the daemon after this many jobs. 0 disables."""
    return int(get("session_max_jobs", default=150))


def session_max_seconds():
    """Recycle the daemon after this long. 0 disables."""
    return int(get("session_max_seconds", default=4 * 3600))


def heartbeat_stale_seconds():
    """Supervisor treats the daemon as hung after this much heartbeat silence."""
    return int(get("heartbeat_stale_seconds", default=90))


def describe():
    cfg = load()
    lines = ["config: %s" % cfg.get("_config_path")]
    for label, fn in [
        ("project", project_path),
        ("source_root", source_root),
        ("state_dir", state_dir),
        ("codesys_exe", codesys_exe),
        ("profile", codesys_profile),
        ("culture", codesys_culture),
        ("rpc", lambda: "%s:%d" % (rpc_host(), rpc_port())),
        ("plc", lambda: "%s:%d" % (plc_host(), plc_port())),
    ]:
        try:
            lines.append("  %-14s %s" % (label, fn()))
        except Exception as ex:
            lines.append("  %-14s <unset: %s>" % (label, ex))
    return "\n".join(lines)

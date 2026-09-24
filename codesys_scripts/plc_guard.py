# -*- coding: ascii -*-
# plc_guard.py -- login policy for daemon jobs.
#
# Why this exists (2026-09-25): a helper job logged in with
# OnlineChangeOption.Try right after an array-sizing CONSTANT went
# 10 -> 32. CODESYS turned that login into a code transfer to the
# RUNNING machine, the IDE hung, it was killed mid-transfer, and the PLC's
# RTOS stopped answering entirely (no ping, telnet or 11740). Only a
# power cycle at the machine could bring it back.
#
# What the login options really do (measured on CODESYS Control Win,
# 3.5.22, app running, project changed; see memory plc-download-pitfalls):
#
#   Keep, Try, Force   code change   -> ONLINE CHANGE, app keeps running
#   Never              code change   -> STOP + full DOWNLOAD
#   Keep               device config -> STOP + full DOWNLOAD
#
# So there is NO scripting login that just attaches. Any login applies
# the difference between the project and the PLC. The only way to log in
# without touching the PLC is to make sure there is no difference.
#
# Hence the policy. Every job runs with a declared PLC mode, and the
# daemon hands it an `online` object whose login() enforces it:
#
#   keep           (default) The project must match what was last
#                  deployed (fingerprint below), else the login is
#                  refused. Try is downgraded to Keep; Force and Never
#                  are refused. The job can read, write, force, start
#                  and stop, but not change the code on the PLC.
#   online_change  Try/Keep allowed (rpc.py push, after its layout
#                  check). Never/Force refused.
#   download       anything allowed (rpc.py install --on-site only).
#
# After a successful login in online_change or download mode the project
# fingerprint is recorded as what the PLC now runs.
#
# This file runs inside CODESYS (IronPython 2.7): keep it py2-clean.

import hashlib
import json
import os
import time

KEEP = "keep"
ONLINE_CHANGE = "online_change"
DOWNLOAD = "download"
MODES = (KEEP, ONLINE_CHANGE, DOWNLOAD)


class GuardError(Exception):
    pass


def option_name(opt):
    """'Try' for OnlineChangeOption.Try (the .NET enum prints its member
    name; a plain string is accepted too so tests can run in CPython)."""
    return str(opt).split(".")[-1]


# ---- project fingerprint ---------------------------------------------

def fingerprint(proj):
    """SHA-1 over everything a login would transfer: POU/GVL/DUT text,
    device identities and parameters, task settings. ~2 s for PackerX."""
    h = hashlib.sha1()

    def put(s):
        try:
            if isinstance(s, unicode):
                s = s.encode("utf-8", "replace")
        except NameError:          # CPython 3 (tests)
            s = s.encode("utf-8", "replace") if isinstance(s, str) else s
        h.update(s)
        h.update(b"\0")

    def walk(o, path):
        p = path + "/" + o.get_name()
        put(p)
        try:
            if o.has_textual_declaration:
                put(o.textual_declaration.text)
            if o.has_textual_implementation:
                put(o.textual_implementation.text)
        except Exception:
            pass
        try:
            if o.is_device:
                di = o.get_device_identification()
                put("dev %s|%s|%s" % (di.type, di.id, di.version))
                for c in o.connectors:
                    for prm in list(c.host_parameters):
                        put("%s=%s" % (prm.name, prm.value))
        except Exception:
            pass
        try:
            put("task %s %s %s %s %s" % (o.priority, o.interval,
                                         o.interval_unit, o.kind_of_task,
                                         o.watchdog.enabled))
        except Exception:
            pass
        for c in o.get_children(False):
            walk(c, p)

    for top in proj.get_children(False):
        walk(top, "")
    return h.hexdigest()


class ProjectSync(object):
    """Remembers the fingerprint of what the PLC runs, in a JSON file."""

    def __init__(self, path, project_fn):
        self.path = path
        self.project_fn = project_fn

    def deployed(self):
        try:
            f = open(self.path, "r")
            try:
                return json.load(f)
            finally:
                f.close()
        except (IOError, OSError, ValueError):
            return None

    def current(self):
        return fingerprint(self.project_fn())

    def check(self):
        """(ok, why). ok means a login cannot change the PLC."""
        dep = self.deployed()
        if dep is None:
            return False, ("nothing recorded as deployed (%s); install or "
                           "push first, or `rpc.py mark-synced` if you KNOW "
                           "the PLC runs this project" % self.path)
        cur = self.current()
        if cur != dep.get("fp"):
            return False, ("the project differs from what the PLC runs "
                           "(deployed %s by %s)" % (dep.get("recorded"),
                                                    dep.get("how")))
        return True, "project matches the PLC"

    def record(self, how):
        data = {"fp": self.current(), "how": how,
                "recorded": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.path + ".tmp"
        f = open(tmp, "w")
        try:
            json.dump(data, f)
        finally:
            f.close()
        if os.path.exists(self.path):
            os.remove(self.path)
        os.rename(tmp, self.path)
        return data


# ---- the guard -------------------------------------------------------

class Guard(object):
    def __init__(self, mode, keep_option, sync, set_phase=None, log=None,
                 label=""):
        if mode not in MODES:
            raise GuardError("unknown plc mode %r (want one of %s)"
                             % (mode, ", ".join(MODES)))
        self.mode = mode
        self.keep_option = keep_option
        self.sync = sync
        self.set_phase = set_phase or (lambda phase: None)
        self.log = log or (lambda msg: None)
        self.label = label

    def login(self, real, opt, args):
        name = option_name(opt)
        if name not in ("Keep", "Try", "Force", "Never"):
            raise GuardError("unknown OnlineChangeOption %r" % (name,))

        if self.mode == KEEP:
            if name in ("Force", "Never"):
                raise GuardError(
                    "login(%s) transfers code, and this job is not marked "
                    "for it. Deploy with `rpc.py push` (online change) or "
                    "`rpc.py install --on-site` (download)." % name)
            if name == "Try":
                self.log("[plc-guard] login(Try) -> login(Keep)")
            if not real.is_logged_in:
                ok, why = self.sync.check()
                if not ok:
                    raise GuardError(
                        "login refused: %s. Any login would online-change or "
                        "download that difference to the PLC." % why)
            return real.login(self.keep_option, *args)

        if self.mode == ONLINE_CHANGE and name in ("Force", "Never"):
            raise GuardError("login(%s) refused in online_change mode; "
                             "use Try" % name)

        phase = "plc:" + self.mode
        self.set_phase(phase)
        try:
            result = real.login(opt, *args)
        finally:
            self.set_phase("running")
        dep = self.sync.record("%s %s" % (self.mode, self.label))
        self.log("[plc-guard] deployed fingerprint recorded: %s"
                 % dep["fp"][:12])
        return result


class GuardedApp(object):
    """Stands in for a ScriptOnlineApplication. Everything but login()
    is passed straight through."""

    def __init__(self, real, guard):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_guard", guard)

    def login(self, opt, *args):
        return self._guard.login(self._real, opt, args)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        setattr(self._real, name, value)


class GuardedOnline(object):
    """Stands in for the `online` global CODESYS injects into scripts."""

    def __init__(self, real, guard):
        self._real = real
        self._guard = guard

    def create_online_application(self, *args, **kwargs):
        real = self._real.create_online_application(*args, **kwargs)
        return GuardedApp(real, self._guard)

    def __getattr__(self, name):
        return getattr(self._real, name)

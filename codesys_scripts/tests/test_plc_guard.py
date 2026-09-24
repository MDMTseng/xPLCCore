# Offline tests for the daemon's login guard and the layout check. No
# PLC or CODESYS needed: the online application is faked.
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import layout_check as L  # noqa: E402
import plc_guard as G  # noqa: E402


class FakeApp:
    def __init__(self, state="run"):
        self.application_state = state
        self.is_logged_in = False
        self.logins = []
        self.timeout = 0

    def login(self, opt, *args):
        self.logins.append(opt)
        self.is_logged_in = True

    def read_value(self, sym):
        return "42"


class FakeOnline:
    def __init__(self, app):
        self.app = app

    def create_online_application(self, _app):
        return self.app


class FakeSync:
    def __init__(self, synced):
        self.synced = synced
        self.recorded = []

    def check(self):
        return self.synced, "synced" if self.synced else "project differs"

    def record(self, how):
        self.recorded.append(how)
        return {"fp": "abcdef0123456789"}


def guarded(mode, synced=True):
    app = FakeApp()
    phases = []
    sync = FakeSync(synced)
    guard = G.Guard(mode, "Keep", sync, set_phase=phases.append, label="t")
    oapp = G.GuardedOnline(FakeOnline(app), guard).create_online_application(
        None)
    return oapp, app, phases, sync


def test_keep_mode_turns_try_into_keep_when_synced():
    oapp, app, phases, sync = guarded(G.KEEP)
    oapp.login("OnlineChangeOption.Try", False)
    assert app.logins == ["Keep"]
    assert phases == [] and sync.recorded == []


@pytest.mark.parametrize("opt", ["Keep", "Try"])
def test_keep_mode_refuses_any_login_when_project_differs(opt):
    # Keep is not read-only: with a difference it online-changes or
    # downloads (measured 2026-09-25), so it must not happen at all.
    oapp, app, _, _ = guarded(G.KEEP, synced=False)
    with pytest.raises(G.GuardError, match="differs"):
        oapp.login(opt, False)
    assert app.logins == []


def test_keep_mode_skips_the_check_when_already_logged_in():
    oapp, app, _, _ = guarded(G.KEEP, synced=False)
    app.is_logged_in = True
    oapp.login("Keep", False)
    assert app.logins == ["Keep"]


@pytest.mark.parametrize("opt", ["Force", "Never"])
def test_keep_mode_refuses_transfers(opt):
    oapp, app, _, _ = guarded(G.KEEP)
    with pytest.raises(G.GuardError):
        oapp.login(opt, False)
    assert app.logins == []


def test_online_change_passes_try_and_records():
    oapp, app, phases, sync = guarded(G.ONLINE_CHANGE, synced=False)
    oapp.login("Try", False)
    assert app.logins == ["Try"]
    assert phases == ["plc:online_change", "running"]
    assert sync.recorded == ["online_change t"]


@pytest.mark.parametrize("opt", ["Force", "Never"])
def test_online_change_refuses_downloads(opt):
    oapp, app, _, sync = guarded(G.ONLINE_CHANGE)
    with pytest.raises(G.GuardError):
        oapp.login(opt, False)
    assert app.logins == [] and sync.recorded == []


def test_download_passes_and_records():
    oapp, app, phases, sync = guarded(G.DOWNLOAD, synced=False)
    oapp.login("Never", False)
    assert app.logins == ["Never"]
    assert phases == ["plc:download", "running"]
    assert sync.recorded == ["download t"]


def test_failed_transfer_records_nothing():
    oapp, app, phases, sync = guarded(G.DOWNLOAD)

    def boom(opt, *a):
        raise RuntimeError("no connection")
    app.login = boom
    with pytest.raises(RuntimeError):
        oapp.login("Never", False)
    assert sync.recorded == []
    assert phases == ["plc:download", "running"]


def test_everything_else_passes_through():
    oapp, app, _, _ = guarded(G.KEEP)
    assert oapp.read_value("GVL.x") == "42"
    oapp.timeout = 5000
    assert app.timeout == 5000


def test_unknown_mode_rejected():
    with pytest.raises(G.GuardError):
        G.Guard("yolo", "Keep", FakeSync(True))


PROGRAM = """
PROGRAM P
    VAR CONSTANT
        N: UDINT := %d; // size
    END_VAR
    VAR
        buf: ARRAY[0..N-1] OF INT;
        x: INT;%s
    END_VAR
"""


def sig(text):
    return {"a.st": {"kind": L.kind_of(text), "sig": L.signature(text)}}


def test_constant_change_is_layout():
    base = sig(PROGRAM % (10, ""))
    assert L.compare(base, sig(PROGRAM % (32, "")))


def test_plain_variable_is_not_layout():
    base = sig(PROGRAM % (10, ""))
    assert L.compare(base, sig(PROGRAM % (10, "\n        y: INT;"))) == []


def test_new_array_is_layout():
    base = sig(PROGRAM % (10, ""))
    cur = sig(PROGRAM % (10, "\n        z: ARRAY[0..3] OF INT;"))
    assert L.compare(base, cur)


def test_comment_edits_are_not_layout():
    base = sig(PROGRAM % (10, ""))
    cur = sig((PROGRAM % (10, "")).replace("// size", "// bigger note"))
    assert L.compare(base, cur) == []


def test_type_change_and_new_type():
    t1 = "TYPE S :\nSTRUCT\n  a: INT;\nEND_STRUCT\nEND_TYPE\n"
    t2 = t1.replace("a: INT;", "a: INT;\n  b: INT;")
    assert L.compare(sig(t1), sig(t2))
    assert L.compare({}, sig(t1))


def test_method_locals_are_ignored():
    m = "METHOD M : BOOL\nVAR\n  tmp: ARRAY[0..%d] OF INT;\nEND_VAR\n"
    assert L.compare(sig(m % 3), sig(m % 9)) == []


def test_retain_block_change_is_layout():
    g = "VAR_GLOBAL RETAIN\n  r: INT;%s\nEND_VAR\n"
    assert L.compare(sig(g % ""), sig(g % "\n  r2: INT;"))

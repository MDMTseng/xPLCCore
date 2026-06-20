"""Full conveyor pick-and-place cycle viz, v2.

Virtual axes self-halt after every G1 (GroupErrorStop=TRUE) so a chain
of G1s only sees the first one execute. Workaround: reset cycle
between each motion phase (EV_ERROR -> EV_RESET -> POWER_ON ->
GROUP_ENABLE -> HOME_FORCE_SKIP). For frame=1 phases we also re-issue
COORD1_UNBIND + COORD1_BIND so tcb sees a clean rising edge linking
PCS_1 to the (now further-advanced) belt point.

Sample throughout via SYS/GET_COORD1_DEBUG + GET_MACHINE_STATE so we
see arm position even during the halt window. Annotate each phase
boundary on the trajectory.
"""
import os, sys, socket, time, subprocess, tempfile
import msgpack
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)
OUT = os.path.join(REPO, "viz_full_cycle.png")

_id = [40000]
def nid():
    _id[0] += 1; return _id[0]


def daemon_exec(code):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(code); path = f.name
    try:
        return subprocess.check_output(RPC + ["exec", "--file", path],
                                       stderr=subprocess.STDOUT).decode("utf-8","replace")
    finally:
        os.unlink(path)


def send_recv(sock, payload, timeout=0.5):
    payload.setdefault("id", nid())
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    pid = payload["id"]; unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout; sock.settimeout(0.12)
    while time.time() < deadline:
        try: d = sock.recv(4096)
        except socket.timeout: continue
        if not d: return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == pid:
                return obj
    return None


EV_POWER_ON, EV_GROUP_ENABLE, EV_HOME_FSK, EV_RESET, EV_ERROR = 2, 4, 7, 8, 9
def st(sock):
    r = send_recv(sock, {"type":"SYS","cmd":"GET_MACHINE_STATE"})
    return r.get("st") if r else None
def ping(sock):
    send_recv(sock, {"type":"SYS","cmd":"PING"}, 0.15)
def gaev(sock, e):
    send_recv(sock, {"type":"SYS","cmd":"GA_EV","ev":e})


def sample(sock):
    ts = time.time()
    d = send_recv(sock, {"type":"SYS","cmd":"GET_COORD1_DEBUG"}, 0.25)
    if not d: return None
    m = send_recv(sock, {"type":"SYS","cmd":"GET_MACHINE_STATE"}, 0.25)
    return {
        "t": ts,
        "arm_x": d.get("arm_x"), "arm_y": d.get("arm_y"), "arm_z": d.get("arm_z"),
        "pcs_x": d.get("kernel_origin_x"),
        "pcs_y": d.get("kernel_origin_y"),
        "pcs_z": d.get("kernel_origin_z"),
        "pulse": d.get("pulse_raw"),
        "bound": d.get("bound"),
        "buf":   m.get("motion_buffer_size") if m else None,
        "mid":   m.get("movement_id") if m else None,
        "st":    m.get("st") if m else None,
    }


def cycle_virtual_mode(samples):
    """Re-arm of virtual drives via the TON-gate cycle. Resets the
    halt latch but NOT the SM3 virtual-axis internal state -- on
    virtual axes subsequent G1s after the first one still don't
    actually move, even though they ACK and movement_id increments.
    This is an SM3 virtual-axis simulation limit (see memory
    smc-virtual-axis-self-halt); the full cycle viz is best taken as
    a structure/protocol demo on virtual hardware, with end-to-end
    motion verification deferred to real EC drives."""
    daemon_exec("""
import time
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","FALSE")
oapp.force_prepared_values()
time.sleep(0.4)
oapp.unforce_all_values()
time.sleep(0.2)
oapp.set_prepared_value("GVL.bVirtualMotorsMode_Request","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","5")
oapp.force_prepared_values()
for _ in range(20):
    v = str(oapp.read_value("GVL.bVirtualMotorsMode"))
    if v.endswith("TRUE"): break
    time.sleep(0.1)
""")


def reset_to_ready(sock, samples, sample_during=True, cycle_vmotors=False):
    """Mini reset cycle. Returns True when state=70.
    cycle_vmotors=True does the full TON-gate re-arm via daemon (slow
    but actually clears virtual-axis halt)."""
    if cycle_vmotors:
        cycle_virtual_mode(samples)
    gaev(sock, EV_ERROR); time.sleep(0.25); ping(sock)
    gaev(sock, EV_RESET); time.sleep(0.25); ping(sock)
    if sample_during:
        s = sample(sock)
        if s: samples.append(s)
    last_ev = 0; last_ping = 0
    deadline = time.time() + 6.0
    while time.time() < deadline:
        sst = st(sock)
        if sst == 70: return True
        if time.time() - last_ping > 0.5: ping(sock); last_ping = time.time()
        if time.time() - last_ev > 0.6:
            if sst == 10:  gaev(sock, EV_POWER_ON);    last_ev = time.time()
            elif sst == 30: gaev(sock, EV_GROUP_ENABLE); last_ev = time.time()
            elif sst == 50: gaev(sock, EV_HOME_FSK);    last_ev = time.time()
        if sample_during:
            s = sample(sock)
            if s: samples.append(s)
        time.sleep(0.05)
    return False


def issue_g1_and_wait(sock, samples, frame, x, y, z, name, F=400, ACC=5000):
    r = send_recv(sock, {"type":"M","cmd":"G1",
                          "frame": frame, "X": x, "Y": y, "Z": z,
                          "F": F, "ACC": ACC, "DEA": ACC, "JERK": ACC*10,
                          "abort": False})
    print("    %s G1: %s" % (name, r))
    if r is None or not r.get("ack"):
        return False
    # Sample while motion executes. Done when buf returns to 0 after going > 0.
    saw_motion = False
    deadline = time.time() + 3.5
    while time.time() < deadline:
        s = sample(sock)
        if s:
            samples.append(s)
            if s["buf"] is not None and s["buf"] > 0:
                saw_motion = True
            if saw_motion and s["buf"] == 0:
                # Tail wait so the FB has time to assert Done internally.
                time.sleep(0.05)
                return True
        time.sleep(0.025)
    return False


def rebind(sock, samples):
    """UNBIND + BIND so tcb gets clean edge with current pulse as ref."""
    s = sample(sock)
    if s: samples.append(s)
    cur_pulse = int(s["pulse"]) if s else 0
    send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})
    time.sleep(0.1)
    r = send_recv(sock, {"type":"SYS","cmd":"COORD1_BIND",
                         "ref_pulse": cur_pulse,
                         "ref_xyz":   [300.0, 50.0, 0.0],
                         "scale":     [100.0, 0.0, 0.0]})
    print("    rebind @ pulse=%d -> %s" % (cur_pulse, r))
    time.sleep(0.2)  # tcb edge + Done


PHASES = [
    {"name": "APPROACH",  "frame": 1, "xyz": (0.0, 0.0, -150.0)},
    {"name": "PICK",      "frame": 1, "xyz": (0.0, 0.0, -200.0)},
    {"name": "LIFT_TRK",  "frame": 1, "xyz": (0.0, 0.0, -150.0)},
    {"name": "UNBIND",    "frame": None},  # SYS step, no motion
    {"name": "TRANSIT",   "frame": 0, "xyz": (-100.0, -150.0, -150.0)},
    {"name": "PLACE",     "frame": 0, "xyz": (-100.0, -150.0, -200.0)},
    {"name": "LIFT_DROP", "frame": 0, "xyz": (-100.0, -150.0, -150.0)},
    {"name": "HOME",      "frame": 0, "xyz": (0.0, 0.0, -150.0)},
]


def main():
    sock = socket.socket(); sock.connect(PLC); sock.settimeout(2.0)
    try: return _inner(sock)
    finally:
        try: send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})
        except: pass
        try:
            daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.force_prepared_values()
import time; time.sleep(0.1); oapp.unforce_all_values()
""")
        except: pass
        sock.close()


def _inner(sock):
    samples = []
    events  = []

    print("--- enable synthetic belt step=5 (slow, 50 mm/s) ---")
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","5")
oapp.force_prepared_values()
""")
    time.sleep(0.2)

    print("--- initial reset to Ready ---")
    if not reset_to_ready(sock, samples):
        print("could not reach Ready"); return 1
    send_recv(sock, {"type":"M","cmd":"SetCoord0"})

    bound_now = False
    for ph in PHASES:
        events.append({"t": time.time(), "name": ph["name"]})
        print("=== %s ===" % ph["name"])

        if ph["name"] == "UNBIND":
            send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})
            bound_now = False
            time.sleep(0.15)
            continue

        # Motion phase. Cycle virtual motors + state machine to actually
        # re-arm the axes (state=70 isn't enough; halt persists internally).
        if not reset_to_ready(sock, samples, cycle_vmotors=True):
            print("    reset failed -- aborting"); break
        send_recv(sock, {"type":"M","cmd":"SetCoord0"})

        if ph["frame"] == 1:
            rebind(sock, samples)
            bound_now = True

        x, y, z = ph["xyz"]
        issue_g1_and_wait(sock, samples, ph["frame"], x, y, z,
                          ph["name"], F=400, ACC=8000)
        # Sample a bit more so the arm-arrived plateau shows.
        for _ in range(8):
            s = sample(sock)
            if s: samples.append(s)
            time.sleep(0.03)

    events.append({"t": time.time(), "name": "END"})
    print("\ncollected %d samples, %d events" % (len(samples), len(events)))
    render(samples, events, OUT)
    print("wrote", OUT)
    return 0


PHASE_COLORS = {
    "APPROACH":  "#ff8800", "PICK": "#ee0000", "LIFT_TRK":  "#ff8800",
    "UNBIND":    "#888888", "TRANSIT":  "#0088ff", "PLACE": "#005500",
    "LIFT_DROP": "#00aa00", "HOME":     "#aa00ff", "END":   "#444444",
}


def render(samples, events, out_path):
    if not samples:
        print("no samples"); return
    t0 = samples[0]["t"]
    T = [s["t"]-t0 for s in samples]

    arm_x = [s["arm_x"] for s in samples]
    arm_y = [s["arm_y"] for s in samples]
    arm_z = [s["arm_z"] for s in samples]
    pcs_x = [s["pcs_x"] for s in samples]
    pcs_y = [s["pcs_y"] for s in samples]

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    ax_xy, ax_xz = axes[0,0], axes[0,1]
    ax_t,  ax_meta = axes[1,0], axes[1,1]

    # XY top-down
    ax_xy.plot(pcs_x, pcs_y, "r-", lw=1.0, alpha=0.5, label="PCS_1 origin (belt)")
    ax_xy.plot(arm_x, arm_y, "b-", lw=1.6, label="arm TCP")
    ax_xy.scatter([arm_x[0]], [arm_y[0]], s=80, marker="s", facecolor="b", alpha=0.4, label="start")
    ax_xy.scatter([arm_x[-1]], [arm_y[-1]], s=140, marker="o", facecolor="none", edgecolor="b", lw=2, label="end")
    # Annotate phase entry points on arm trajectory
    for ev in events:
        et = ev["t"] - t0
        # find sample closest to event time
        idx = min(range(len(T)), key=lambda i: abs(T[i] - et))
        ax_xy.annotate(ev["name"], (arm_x[idx], arm_y[idx]),
                       fontsize=7, color=PHASE_COLORS.get(ev["name"], "k"))
    ax_xy.set_xlabel("WCS X (mm)"); ax_xy.set_ylabel("WCS Y (mm)")
    ax_xy.set_title("XY top-down (arm in blue, belt in red)")
    ax_xy.legend(loc="best"); ax_xy.grid(True, alpha=0.3)
    ax_xy.set_aspect("equal", adjustable="datalim")

    # XZ side view
    ax_xz.plot(arm_x, arm_z, "b-", lw=1.6, label="arm")
    ax_xz.scatter([arm_x[0]], [arm_z[0]], s=80, marker="s", facecolor="b", alpha=0.4, label="start")
    ax_xz.scatter([arm_x[-1]], [arm_z[-1]], s=140, marker="o", facecolor="none", edgecolor="b", lw=2, label="end")
    for ev in events:
        et = ev["t"] - t0
        idx = min(range(len(T)), key=lambda i: abs(T[i] - et))
        ax_xz.annotate(ev["name"], (arm_x[idx], arm_z[idx]),
                       fontsize=7, color=PHASE_COLORS.get(ev["name"], "k"))
    ax_xz.set_xlabel("WCS X (mm)"); ax_xz.set_ylabel("WCS Z (mm)")
    ax_xz.set_title("XZ side view (Z-200 = pick depth)")
    ax_xz.legend(loc="best"); ax_xz.grid(True, alpha=0.3)

    # XYZ vs time
    ax_t.plot(T, arm_x, "b-",  lw=1.4, label="arm X")
    ax_t.plot(T, arm_y, "g-",  lw=1.4, label="arm Y")
    ax_t.plot(T, arm_z, "m-",  lw=1.4, label="arm Z")
    ax_t.plot(T, pcs_x, "r--", lw=0.8, alpha=0.6, label="PCS_1 X (belt)")
    for ev in events:
        et = ev["t"] - t0
        ax_t.axvline(et, color=PHASE_COLORS.get(ev["name"], "k"), lw=0.5, alpha=0.4)
        ax_t.text(et, ax_t.get_ylim()[1] if ax_t.get_ylim()[1] else 1,
                  ev["name"], rotation=90, fontsize=7, va="top",
                  color=PHASE_COLORS.get(ev["name"], "k"))
    ax_t.set_xlabel("t (s)"); ax_t.set_ylabel("mm")
    ax_t.set_title("Arm X/Y/Z + belt X vs time")
    ax_t.legend(loc="best", ncol=2); ax_t.grid(True, alpha=0.3)

    last = samples[-1]
    ax_meta.axis("off")
    lines = [
        "samples: %d  duration: %.2f s" % (len(samples), T[-1]),
        "",
        "events:",
    ]
    for ev in events:
        lines.append("  %5.2f s  %s" % (ev["t"]-t0, ev["name"]))
    lines += [
        "",
        "final pulse: %s   bound: %s" % (last["pulse"], last["bound"]),
        "final arm TCP: %.2f %.2f %.2f" % (last["arm_x"], last["arm_y"], last["arm_z"]),
        "final PCS_1:   %.2f %.2f %.2f" % (last["pcs_x"], last["pcs_y"], last["pcs_z"]),
    ]
    ax_meta.text(0.02, 0.98, "\n".join(lines), va="top",
                 family="monospace", fontsize=9)

    fig.suptitle("Conveyor pick & place full cycle (per-G1 reset to dodge virtual-axis halt)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())

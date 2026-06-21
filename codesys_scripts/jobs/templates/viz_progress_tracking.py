"""Probe: how does MovementProgress evolve under a moving target?

Sequence:
  1. Drive PLC to Ready, virtual mode, BIND (ref_pulse = current)
  2. Enable synthetic belt @ step=5 (~50 mm/s)
  3. Issue ONE G1 frame=1 X=0 Y=0 Z=-145 (hover above belt point)
  4. Sample ~50 Hz via SYS/GET_COORD1_DEBUG: mv_id, mv_progress, arm,
     PCS_1 origin, pulse
  5. Stop sampling when motion_buffer_size returns to 0 AND has been
     0 for >0.5 s -- or 4 s timeout
  6. Plot mv_progress vs time, arm-to-target distance vs time, arm X /
     belt X vs time. The question is: when does progress hit 1, and
     does it stay there as belt advances?
"""
import os, sys, socket, time, subprocess, tempfile
import msgpack
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)
OUT = os.path.join(REPO, "viz_progress_tracking.png")

_id = [60000]
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
def ping(sock): send_recv(sock, {"type":"SYS","cmd":"PING"}, 0.2)

def drive_to_ready(sock):
    def ev(e): send_recv(sock, {"type":"SYS","cmd":"GA_EV","ev":e})
    ev(EV_ERROR); time.sleep(0.3); ping(sock)
    ev(EV_RESET); time.sleep(0.3); ping(sock)
    last_st = None; last_ping = 0; last_ev = 0
    deadline = time.time() + 20
    while time.time() < deadline:
        s = st(sock)
        if s != last_st: print("    state ->", s); last_st = s
        if s == 70: return True
        if time.time() - last_ping > 0.8: ping(sock); last_ping = time.time()
        if time.time() - last_ev > 1.2:
            if s == 10: ev(EV_POWER_ON); last_ev = time.time()
            elif s == 30: ev(EV_GROUP_ENABLE); last_ev = time.time()
            elif s == 50: ev(EV_HOME_FSK); last_ev = time.time()
        time.sleep(0.2)
    return False


def sample(sock):
    ts = time.time()
    d = send_recv(sock, {"type":"SYS","cmd":"GET_COORD1_DEBUG"}, 0.3)
    if not d: return None
    m = send_recv(sock, {"type":"SYS","cmd":"GET_MACHINE_STATE"}, 0.3)
    return {
        "t": ts,
        "arm_x": d.get("arm_x"), "arm_y": d.get("arm_y"), "arm_z": d.get("arm_z"),
        "pcs_x": d.get("kernel_origin_x"),
        "pcs_y": d.get("kernel_origin_y"),
        "pcs_z": d.get("kernel_origin_z"),
        "pulse": d.get("pulse_raw"),
        "mv_id": d.get("mv_id"),
        "mv_progress": d.get("mv_progress"),
        "buf":   m.get("motion_buffer_size") if m else None,
        "st":    m.get("st") if m else None,
        "err_id": m.get("err_id") if m else 0,
        "err_src": m.get("err_src") if m else "",
    }


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
    print("--- drive to Ready ---")
    if not drive_to_ready(sock):
        print("could not reach Ready"); return 1
    send_recv(sock, {"type":"M","cmd":"SetCoord0"})
    send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})

    # Reset pulse to 0 then enable belt motion BEFORE bind.
    daemon_exec("""
import time
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","FALSE")
oapp.set_prepared_value("GVL.ConveyorPulseRaw","0")
oapp.force_prepared_values(); time.sleep(0.1)
oapp.unforce_all_values(); time.sleep(0.1)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","30")  # ~300 mm/s belt
oapp.force_prepared_values()
""")
    time.sleep(0.3)

    s0 = sample(sock)
    cur_pulse = int(s0["pulse"])
    print("--- BIND @ pulse=%d ---" % cur_pulse)
    r = send_recv(sock, {"type":"SYS","cmd":"COORD1_BIND",
                         "ref_pulse": cur_pulse,
                         "ref_xyz":   [10.0, 0.0, -150.0],
                         "scale":     [100.0, 0.0, 0.0]})
    print("    bind reply:", r)
    time.sleep(0.25)

    samples = []
    t0 = time.time()
    g1_t = None
    err_events = []   # collect any group_error_stop / NAK observations

    print("--- sampling (slow arm vs fast belt, 10s) ---")
    deadline = t0 + 10.0
    buf_zero_since = None
    while time.time() < deadline:
        s = sample(sock)
        if s: samples.append(s)
        if g1_t is None and (time.time() - t0) > 0.5:
            # PCS_1 frame: Z=+5 hovers 5mm above belt point (which sits
            # at WCS Z=-150 by bind ref_xyz). Reachable inside the
            # delta arm workspace cone.
            # Arm crippled to 10 mm/s; belt running ~300 mm/s. arm
            # CAN'T catch up by design. Watch for: G1 NAK, auto-disengage,
            # error events, or just stuck-progress-forever.
            r = send_recv(sock, {"type":"M","cmd":"G1",
                                  "frame": 1,
                                  "X": 0.0, "Y": 0.0, "Z": 5.0,
                                  "F": 10.0, "ACC": 50.0,
                                  "DEA": 50.0, "JERK": 500.0})
            print("    G1 reply:", r)
            g1_t = time.time()
        # Periodically check for buf=0 collapse (= motion done or auto-
        # disengage). Don't early-exit; we want to observe what happens
        # over the full window even if motion drops out.
        if s and g1_t is not None and s["buf"] == 0 and (time.time()-g1_t) > 0.5:
            if buf_zero_since is None:
                buf_zero_since = time.time()
                err_events.append({"t": time.time(), "what": "buf->0 after G1"})
                print("    [t=%.2f] buf collapsed to 0 (motion ended)" % (time.time()-t0))
        # Also actively probe for an error_stop NAK by sending PING
        # which is SYS (no FSM gate); look at GET_MACHINE_STATE.err_id
        # Already in m -- no extra probe needed
        time.sleep(0.025)

    print("\ncollected %d samples" % len(samples))
    render(samples, (g1_t - t0) if g1_t else None, OUT)
    print("wrote", OUT)
    return 0


def render(samples, g1_t_rel, out_path):
    if not samples: print("no samples"); return
    t0 = samples[0]["t"]
    T = [s["t"]-t0 for s in samples]

    arm_x = [s["arm_x"] for s in samples]
    arm_y = [s["arm_y"] for s in samples]
    arm_z = [s["arm_z"] for s in samples]
    pcs_x = [s["pcs_x"] for s in samples]
    pcs_y = [s["pcs_y"] for s in samples]
    pcs_z = [s["pcs_z"] for s in samples]
    mv_id = [s["mv_id"] for s in samples]
    mv_prog = [s["mv_progress"] for s in samples]
    pulse = [s["pulse"] for s in samples]

    # arm-to-target dist: target is PCS_1 + (0,0,-145-(-150)) = (pcs_x, pcs_y, pcs_z-145)
    # but PCS_1 origin Z is -150 (the bind ref), so target_z = -150 + (-145 - 0)? hmm
    # Actually frame=1 Position=(0,0,-145) in PCS_1 means target_wcs = PCS_1_origin + (0,0,-145)
    # = (pcs_x, pcs_y, pcs_z-145). With pcs_z = -150, target_z = -295. But arm goes to -145.
    # Wait, bind ref_xyz Z=-150 puts PCS_1 Z origin at -150 in WCS. So PCS_1 Z=0 -> WCS Z=-150.
    # Frame=1 Position Z=-145 means PCS_1 Z=-145 -> WCS Z=-150 + (-145) = -295. Out of reach.
    # That's wrong. We want PCS_1 Z=+5 -> WCS Z=-145.
    # Hmm let me reconsider. For this probe, just hover at PCS_1 (0,0,0) = WCS (pcs_x, 0, -150).
    # Let me just compute dist from arm to PCS_1 origin (the moving target):
    dist = []
    for s in samples:
        dx = s["arm_x"] - s["pcs_x"]
        dy = s["arm_y"] - s["pcs_y"]
        dz = s["arm_z"] - s["pcs_z"]
        dist.append((dx*dx + dy*dy + dz*dz) ** 0.5)

    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    ax_prog, ax_xt = axes[0,0], axes[0,1]
    ax_dist, ax_meta = axes[1,0], axes[1,1]

    # Progress vs time
    ax_prog.plot(T, mv_prog, "b-", lw=2.0, label="mv_progress")
    ax_prog.set_ylim(-0.05, 1.1)
    ax_prog.axhline(1.0, color="g", lw=0.5, ls="--", alpha=0.5)
    ax_prog.axhline(0.0, color="r", lw=0.5, ls="--", alpha=0.5)
    if g1_t_rel is not None:
        ax_prog.axvline(g1_t_rel, color="k", lw=0.8, ls=":", label="G1 issued")
    ax_prog.set_xlabel("t (s)"); ax_prog.set_ylabel("progress")
    ax_prog.set_title("CurrentMovementIdProgress over time")
    ax_prog.legend(loc="best"); ax_prog.grid(True, alpha=0.3)

    # X over time: arm vs PCS_1 (belt target)
    ax_xt.plot(T, pcs_x, "r-", lw=1.6, label="PCS_1 origin X (live belt)")
    ax_xt.plot(T, arm_x, "b-", lw=1.6, label="arm X")
    if g1_t_rel is not None:
        ax_xt.axvline(g1_t_rel, color="k", lw=0.8, ls=":", label="G1 issued")
    ax_xt.set_xlabel("t (s)"); ax_xt.set_ylabel("X (mm)")
    ax_xt.set_title("Arm X vs belt target X")
    ax_xt.legend(loc="best"); ax_xt.grid(True, alpha=0.3)

    # arm-to-target distance vs time
    ax_dist.plot(T, dist, "m-", lw=1.6, label="|arm - PCS_1|")
    if g1_t_rel is not None:
        ax_dist.axvline(g1_t_rel, color="k", lw=0.8, ls=":", label="G1 issued")
    ax_dist.set_xlabel("t (s)"); ax_dist.set_ylabel("distance (mm)")
    ax_dist.set_title("Arm distance to live PCS_1 origin")
    ax_dist.legend(loc="best"); ax_dist.grid(True, alpha=0.3)
    ax_dist.set_yscale("log")

    # Meta
    last = samples[-1]
    ax_meta.axis("off")
    # Find when progress first hit >=1.0
    first_done_t = None
    for s, t in zip(samples, T):
        if s["mv_progress"] is not None and s["mv_progress"] >= 0.999:
            first_done_t = t; break
    # Detect any events of note
    err_id_changes = []
    prev_err = 0
    for s, t in zip(samples, T):
        e = s.get("err_id", 0) or 0
        if e != prev_err:
            err_id_changes.append("t=%.2f  err_id %s -> %s  err_src=%s" % (t, prev_err, e, s.get("err_src","")))
            prev_err = e
    buf_history = []
    prev_buf = None
    for s, t in zip(samples, T):
        if s["buf"] != prev_buf:
            buf_history.append("t=%.2f  buf -> %s" % (t, s["buf"]))
            prev_buf = s["buf"]
    mv_id_changes = []
    prev_mv = None
    for s, t in zip(samples, T):
        if s["mv_id"] != prev_mv:
            mv_id_changes.append("t=%.2f  mv_id -> %s" % (t, s["mv_id"]))
            prev_mv = s["mv_id"]
    lines = [
        "samples: %d  duration: %.2f s" % (len(samples), T[-1]),
        "G1 issued at t = %.2f s" % (g1_t_rel or 0),
        "",
        "first time mv_progress >= 0.999: %s" % (
            ("%.2f s (%.2f s after G1)" % (first_done_t, first_done_t - (g1_t_rel or 0)))
            if first_done_t is not None else "NEVER"),
        "",
        "buf timeline:",
    ]
    for l in buf_history[:8]: lines.append("  " + l)
    lines.append("mv_id timeline:")
    for l in mv_id_changes[:8]: lines.append("  " + l)
    lines.append("err_id timeline:")
    for l in err_id_changes[:8]: lines.append("  " + l)
    lines += [
        "",
        "final: mv_id=%s prog=%s buf=%s st=%s err=%s" % (
            last["mv_id"], last["mv_progress"], last["buf"], last["st"], last["err_id"]),
        "arm   = %.2f %.2f %.2f" % (last["arm_x"], last["arm_y"], last["arm_z"]),
        "PCS_1 = %.2f %.2f %.2f" % (last["pcs_x"], last["pcs_y"], last["pcs_z"]),
        "|dist| final = %.3f mm    pulse=%s" % (dist[-1], last["pulse"]),
    ]
    ax_meta.text(0.02, 0.95, "\n".join(lines), va="top",
                 family="monospace", fontsize=10)

    fig.suptitle("MovementProgress under moving belt target (G1 frame=1)", fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())

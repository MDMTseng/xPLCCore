"""Phase 4 step 2 live viz v3: pure-TCP sampling (50 Hz easy), arm
position read via SYS/GET_COORD1_DEBUG so a virtual-axis self-halt
doesn't kill the trace mid-motion.

The arm halts within ~500ms after the first G1 on virtual axes, so
we get one shot per Error/Reset cycle. With high F/ACC the arm gets
far enough to clearly show convergence on the moving belt point
before the halt kicks in.
"""
import os, sys, socket, time, subprocess, tempfile
import msgpack
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
RPC = [sys.executable, os.path.join(REPO, "codesys_scripts", "rpc.py")]
PLC = ("192.168.1.70", 8125)
OUT = os.path.join(REPO, "viz_pcs1_chase.png")

_id = [30000]
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


def send_recv(sock, payload, timeout=0.6):
    payload.setdefault("id", nid())
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    pid = payload["id"]
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout
    sock.settimeout(0.15)
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
    send_recv(sock, {"type":"SYS","cmd":"PING"}, 0.2)


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
    """One snapshot via single SYS/GET_COORD1_DEBUG call."""
    ts = time.time()
    d = send_recv(sock, {"type":"SYS","cmd":"GET_COORD1_DEBUG"}, 0.3)
    if not d: return None
    return {
        "t": ts,
        "arm_x": d.get("arm_x"), "arm_y": d.get("arm_y"), "arm_z": d.get("arm_z"),
        "pcs_x": d.get("kernel_origin_x"),
        "pcs_y": d.get("kernel_origin_y"),
        "pcs_z": d.get("kernel_origin_z"),
        "pred_x": d.get("origin_x"),
        "pulse": d.get("pulse_raw"),
        "bound": d.get("bound"),
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
import time; time.sleep(0.1)
oapp.unforce_all_values()
""")
        except: pass
        sock.close()


def _inner(sock):
    print("--- drive to Ready ---")
    if not drive_to_ready(sock):
        print("could not reach Ready"); return 1
    send_recv(sock, {"type":"M","cmd":"SetCoord0"})
    send_recv(sock, {"type":"SYS","cmd":"COORD1_UNBIND"})

    # Enable belt motion BEFORE bind so tcb sees a moving axis.
    daemon_exec("""
proj = projects.primary
app = next(iter(proj.find("Application", True)))
oapp = online.create_online_application(app)
oapp.login(OnlineChangeOption.Try, False)
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticEnable","TRUE")
oapp.set_prepared_value("GVL.ConveyorPulseSyntheticStep","5")
oapp.force_prepared_values()
""")
    time.sleep(0.3)

    # Read current pulse to use as ref_pulse, so PCS_1 origin starts
    # exactly at ref_xyz=(300,50,0) at bind time.
    s0 = sample(sock)
    cur_pulse = int(s0["pulse"])
    print("    bind ref_pulse =", cur_pulse, " current PCS_1 (kernel)=", s0["pcs_x"])

    r = send_recv(sock, {"type":"SYS","cmd":"COORD1_BIND",
                         "ref_pulse": cur_pulse,
                         "ref_xyz":   [300.0, 50.0, 0.0],
                         "scale":     [100.0, 0.0, 0.0]})
    print("    bind reply:", r)
    time.sleep(0.25)  # tcb edge + Done

    # Sample baseline (~0.5 s) before issuing G1.
    samples = []
    t0 = time.time()
    g1_t = None
    print("--- sampling 3 s, G1 at t=0.5 ---")
    while time.time() - t0 < 3.0:
        s = sample(sock)
        if s:
            s["g1_in_flight"] = (g1_t is not None and time.time() - g1_t < 5.0)
            samples.append(s)
        if g1_t is None and (time.time() - t0) > 0.5:
            # Target PCS_1 (0, 0, -100) -- 100mm below the belt point, in
            # the delta arm's reachable workspace cone. WCS target at
            # G1 issue ~= (300, 50, -100); kernel re-aims as belt moves.
            r = send_recv(sock, {"type":"M","cmd":"G1",
                                  "frame": 1,
                                  "X": 0.0, "Y": 0.0, "Z": -100.0,
                                  "F": 600.0, "ACC": 8000.0, "DEA": 8000.0,
                                  "JERK": 80000.0})
            print("    G1 reply:", r)
            g1_t = time.time()
        time.sleep(0.02)

    print("collected %d samples; rendering..." % len(samples))
    render(samples, (g1_t - t0) if g1_t else None, OUT)
    print("wrote", OUT)
    return 0


def render(samples, g1_t_rel, out_path):
    if not samples: print("no samples"); return
    t0 = samples[0]["t"]
    T = [s["t"]-t0 for s in samples]

    pcs_x = [s["pcs_x"] for s in samples]
    pcs_y = [s["pcs_y"] for s in samples]
    pcs_z = [s["pcs_z"] for s in samples]
    arm_x = [s["arm_x"] for s in samples]
    arm_y = [s["arm_y"] for s in samples]
    arm_z = [s["arm_z"] for s in samples]
    pred_x = [s["pred_x"] for s in samples]
    tgt_z = [z - 100 for z in pcs_z]

    fig, axes = plt.subplots(2, 2, figsize=(15, 9))
    ax_xy, ax_xt = axes[0,0], axes[0,1]
    ax_zt, ax_meta = axes[1,0], axes[1,1]

    ax_xy.plot(pcs_x, pcs_y, "r-", lw=1.6, label="PCS_1 origin (belt)")
    ax_xy.plot(arm_x, arm_y, "b-", lw=1.6, label="arm TCP")
    ax_xy.scatter([arm_x[-1]], [arm_y[-1]], s=140, marker="o",
                  facecolor="none", edgecolor="b", lw=2, zorder=10)
    ax_xy.scatter([pcs_x[-1]], [pcs_y[-1]], s=140, marker="o",
                  facecolor="none", edgecolor="r", lw=2, zorder=10)
    ax_xy.scatter([arm_x[0]], [arm_y[0]], s=80, marker="s",
                  facecolor="b", alpha=0.4, zorder=9)
    ax_xy.set_xlabel("WCS X (mm)"); ax_xy.set_ylabel("WCS Y (mm)")
    ax_xy.set_title("Top-down XY: arm (blue) chasing PCS_1 origin (red)")
    ax_xy.legend(loc="best"); ax_xy.grid(True, alpha=0.3)
    ax_xy.set_aspect("equal", adjustable="datalim")

    ax_xt.plot(T, pcs_x, "r-", lw=1.6, label="PCS_1 origin X (belt)")
    ax_xt.plot(T, pred_x, "m--", lw=0.8, alpha=0.5, label="prediction X")
    ax_xt.plot(T, arm_x, "b-", lw=1.6, label="arm X")
    if g1_t_rel is not None:
        ax_xt.axvline(g1_t_rel, color="k", lw=0.8, ls=":", label="G1 issued")
    ax_xt.set_xlabel("t (s)"); ax_xt.set_ylabel("X (mm)")
    ax_xt.set_title("X vs time"); ax_xt.legend(loc="best")
    ax_xt.grid(True, alpha=0.3)

    ax_zt.plot(T, tgt_z, "g--", lw=1.0, label="G1 target Z (PCS_1 Z - 100)")
    ax_zt.plot(T, arm_z, "b-", lw=1.6, label="arm Z")
    if g1_t_rel is not None:
        ax_zt.axvline(g1_t_rel, color="k", lw=0.8, ls=":", label="G1 issued")
    ax_zt.set_xlabel("t (s)"); ax_zt.set_ylabel("Z (mm)")
    ax_zt.set_title("Z vs time"); ax_zt.legend(loc="best")
    ax_zt.grid(True, alpha=0.3)

    last = samples[-1]
    ax_meta.axis("off")
    lines = [
        "samples: %d  duration: %.2f s  rate: %.1f Hz" % (len(samples), T[-1], len(samples)/T[-1] if T[-1]>0 else 0),
        "final pulse: %s   bound: %s" % (last["pulse"], last["bound"]),
        "",
        "final PCS_1 (kernel): %.2f %.2f %.2f" % (last["pcs_x"], last["pcs_y"], last["pcs_z"]),
        "final arm TCP:        %.2f %.2f %.2f" % (last["arm_x"], last["arm_y"], last["arm_z"]),
        "",
        "arm-to-PCS_1 X diff: %+8.2f mm" % (last["arm_x"] - last["pcs_x"]),
        "arm-to-PCS_1 Y diff: %+8.2f mm" % (last["arm_y"] - last["pcs_y"]),
        "arm Z vs target(-100): %+8.2f mm" % (last["arm_z"] - (-100.0)),
        "",
        "kernel vs prediction X: %+8.3e mm" % (last["pcs_x"] - last["pred_x"]),
    ]
    ax_meta.text(0.02, 0.95, "\n".join(lines), va="top", family="monospace", fontsize=10)

    fig.suptitle("MC_TrackConveyorBelt -- G1 frame=1 chase (v3: SYS-path arm read, 50Hz)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())

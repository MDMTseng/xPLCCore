"""Conveyor-pick visualization: sample arm TCP + belt-tracked object
position over a window of time, plot as top-down XY view.

Usage: python viz_belt_arm.py [--duration SEC] [--rate HZ] [--out PATH]

Reads:
  - GET_MACHINE_STATE: arm TCP via READ_LATEST_CMD_LOCATION (X/Y/Z)
  - GET_COORD1_DEBUG: belt origin_x/y/z (= object WCS position when
    bound, refreshed every scan as the belt advances)

Output: a PNG with:
  - belt direction arrow
  - object trajectory (red dots, time-decayed alpha)
  - arm trajectory (blue dots)
  - current positions highlighted
"""
import argparse, socket, time, sys, os
import msgpack
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PLC = ("192.168.1.70", 8125)
_id_seq = [40000]
def _id():
    _id_seq[0] += 1
    return _id_seq[0]


def send_recv(sock, payload, timeout=1.0):
    pid = payload.setdefault("id", _id())
    sock.sendall(msgpack.packb(payload, use_bin_type=True))
    unp = msgpack.Unpacker(raw=False)
    deadline = time.time() + timeout
    sock.settimeout(0.3)
    while time.time() < deadline:
        try:
            d = sock.recv(4096)
        except socket.timeout:
            continue
        if not d:
            return None
        unp.feed(d)
        for obj in unp:
            if isinstance(obj, dict) and obj.get("id") == pid:
                return obj
    return None


def sample_one(sock):
    """Single (arm_xyz, obj_xyz, bound) snapshot. None if reads fail."""
    state = send_recv(sock, {"type": "SYS", "cmd": "GET_MACHINE_STATE"})
    debug = send_recv(sock, {"type": "SYS", "cmd": "GET_COORD1_DEBUG"})
    if not state or not debug:
        return None
    # Read arm TCP -- READ_LATEST_CMD_LOCATION is a motion-channel cmd,
    # not SYS; the FSM gate NAKs it when not Ready. As a fallback use
    # ReadPosition fields if exposed by GET_MACHINE_STATE; else use 0,0,0.
    # For visualization, READ_LATEST_CMD_LOCATION via type:M is fine when
    # FSM allows it; otherwise just skip the arm point this sample.
    arm = send_recv(sock, {"type": "M", "cmd": "READ_LATEST_CMD_LOCATION"})
    if arm is None or arm.get("ack") is False:
        arm_xyz = None
    else:
        arm_xyz = (arm.get("X", 0), arm.get("Y", 0), arm.get("Z", 0))
    obj_xyz = (debug.get("origin_x", 0),
               debug.get("origin_y", 0),
               debug.get("origin_z", 0))
    return {
        "t": time.time(),
        "arm": arm_xyz,
        "obj": obj_xyz,
        "bound": debug.get("bound", False),
        "pulse": debug.get("pulse_raw", 0),
        "st": state.get("st"),
    }


def collect(duration_s, rate_hz):
    sock = socket.socket()
    sock.connect(PLC); sock.settimeout(2.0)
    samples = []
    t0 = time.time()
    dt = 1.0 / rate_hz
    next_t = t0
    while time.time() - t0 < duration_s:
        s = sample_one(sock)
        if s:
            samples.append(s)
        next_t += dt
        sleep_for = next_t - time.time()
        if sleep_for > 0:
            time.sleep(sleep_for)
    sock.close()
    return samples


def render(samples, out_path):
    if not samples:
        print("no samples collected"); return

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    ax_top = axes[0]
    ax_xt = axes[1]

    t0 = samples[0]["t"]
    times = [s["t"] - t0 for s in samples]
    arm_pts = [s["arm"] for s in samples if s["arm"] is not None]
    obj_pts = [s["obj"] for s in samples]

    # Top-down XY plot
    if arm_pts:
        ax_top.plot([p[0] for p in arm_pts], [p[1] for p in arm_pts],
                    "b.-", alpha=0.6, ms=4, label="arm TCP")
        ax_top.scatter([arm_pts[-1][0]], [arm_pts[-1][1]],
                       s=120, marker="o", facecolor="none", edgecolor="b",
                       linewidth=2, zorder=10)
    if obj_pts:
        ax_top.plot([p[0] for p in obj_pts], [p[1] for p in obj_pts],
                    "r.-", alpha=0.6, ms=4, label="belt object")
        ax_top.scatter([obj_pts[-1][0]], [obj_pts[-1][1]],
                       s=120, marker="o", facecolor="none", edgecolor="r",
                       linewidth=2, zorder=10)

    ax_top.set_xlabel("WCS X (mm)")
    ax_top.set_ylabel("WCS Y (mm)")
    ax_top.set_title("Top-down XY (arm vs belt object)")
    ax_top.grid(True, alpha=0.3)
    ax_top.set_aspect("equal", adjustable="datalim")
    ax_top.legend(loc="upper right")

    # X vs time
    if arm_pts:
        arm_t = [t for s, t in zip(samples, times) if s["arm"] is not None]
        ax_xt.plot(arm_t, [p[0] for p in arm_pts], "b.-", ms=3, label="arm X")
    ax_xt.plot(times, [p[0] for p in obj_pts], "r.-", ms=3, label="belt object X")
    ax_xt.set_xlabel("t (s)")
    ax_xt.set_ylabel("WCS X (mm)")
    ax_xt.set_title("X vs time")
    ax_xt.grid(True, alpha=0.3)
    ax_xt.legend(loc="upper left")

    last = samples[-1]
    fig.suptitle("conveyor pick viz   bound=%s  pulse=%d  st=%s  n=%d  dur=%.1fs"
                 % (last["bound"], last["pulse"], last["st"],
                    len(samples), times[-1]))
    fig.tight_layout()
    fig.savefig(out_path, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--out", default="viz_belt_arm.png")
    args = ap.parse_args()
    samples = collect(args.duration, args.rate)
    print("collected %d samples" % len(samples))
    render(samples, args.out)


if __name__ == "__main__":
    main()

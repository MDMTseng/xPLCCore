# End-to-end movement regression. Mirrors test_movement_sequence.py main()
# but split into pytest cases so individual stages can be debugged.
import pytest

from . import conftest  # noqa: F401  (force fixture discovery on some setups)
import test_movement_sequence as tms


def test_link_alive():
    pong = tms.harness_send({"type": "SYS", "cmd": "PING"}, timeout=3.0)
    assert pong.get("pong") is True, f"no pong: {pong}"


def test_drive_to_ready(fsm_ready):
    st = tms.get_state()
    assert st.get("st") == tms.READY, st


@pytest.mark.parametrize("label,x,y,z", [
    ("home", 0.0,  0.0, 0.0),
    ("p1",  10.0,  0.0, 0.0),
    ("p2",  10.0, 10.0, 0.0),
    ("p3",   0.0, 10.0, 0.0),
    ("back", 0.0,  0.0, 0.0),
])
def test_g1_move(fsm_ready, label, x, y, z):
    mid = tms.do_move(label, x, y, z)
    assert mid is not None, f"move {label} did not complete"


def test_final_state_ready(fsm_ready):
    final = tms.get_state()
    assert final.get("st") == tms.READY, (
        f"final state not Ready: st={final.get('st')} "
        f"err_src={final.get('err_src')} err_id={final.get('err_id')}"
    )
    assert final.get("motion_buffer_size", 0) == 0, final

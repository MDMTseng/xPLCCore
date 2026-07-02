METHOD Coord1CommitBind : BOOL
VAR_INPUT
    // Belt reference point in WCS at refPulse (mm).
    refX : LREAL;
    refY : LREAL;
    refZ : LREAL;
    // Belt scale along WCS +X (pulses per mm). Y/Z must be validated
    // to zero by the CALLER (the wire payloads carry them, this method
    // only sees the X component) -- both call sites reject non-axial
    // belts before calling.
    scaleX : LREAL;
    // Conveyor pulse the reference point is anchored at. SYS path takes
    // it off the wire ('ref_pulse'); fly path anchors at the firing
    // scan's GVL.ConveyorPulseRaw.
    refPulse : DINT;
    // Pick-window budget in pulses. > 0: Coord1ExitPulse := refPulse +
    // offset (window-exit detector armed). <= 0: no window check
    // (Coord1ExitPulse := -1 sentinel -- dev/test SYS binds).
    exitPulseOffset : DINT;
    // Host correlation id echoed in COORD1_ERROR payloads.
    eventId : DINT;
END_VAR
VAR_OUTPUT
    // Reject reason on FALSE return; wire-compatible with the SYS
    // COORD1_BIND NAK err strings.
    RejectReason : STRING(40);
END_VAR
(* =========== IMPLEMENTATION =========== *)
// B3 fix (analysis 2.1, test_plc_refactor_findings_b2): the bind-commit
// used to live TWICE -- in the SYS COORD1_BIND handler (DrainHostPackets)
// and in the FlyEvent COORD1_BIND action (ProcessFlyEventsAndIo) -- and
// had already drifted: the SYS copy refused binds while motion was
// queued, the fly copy silently re-anchored PCS_1 under the in-flight
// move; ExitPulse handling and the validation sets differed too. This
// method is the ONE validation + commit for both paths. Callers keep
// their per-path reporting (SYS: NAK err + Coord1Bind*NakCount; fly:
// fault path via Coord1LatchWindowError + Transition(EV_ERROR)) and
// bump their per-path counters (Coord1BindCount vs Coord1FlyBindCount)
// at the call site.
//
// Validation order: busy -> rebind -> scale. All three refusals are
// identical through both paths by construction.
Coord1CommitBind := FALSE;
RejectReason := '';

// Reject if the motion buffer is non-empty so the active move's coord
// frame can't change mid-flight (the arm would dart to the re-anchored
// PCS_1 target). This is the check the fly path was missing.
IF MotionBufferSize > 0 THEN
    RejectReason := 'coord_bind_busy';
    RETURN;
END_IF

// Reject re-bind while already bound: caller must UNBIND first so
// MC_TrackConveyorBelt sees a clean falling edge on Execute.
IF GVL.Coord1Bound THEN
    RejectReason := 'rebind_requires_unbind';
    RETURN;
END_IF

// Belt must move along WCS +X: a zero X scale means "no belt direction"
// and would divide by zero below.
IF scaleX = 0.0 THEN
    RejectReason := 'belt_scale_x_required';
    RETURN;
END_IF

// Commit: set the calibration + belt origin first, then flip
// Coord1Bound TRUE -- the per-scan trackConveyorBeltFb() at the top of
// the next scan picks up the rising edge with the new origin already in
// place. (Both call sites run after that per-scan block, so an edge
// fired this scan would have used stale data.)
Coord1PulseToMm := 1.0 / scaleX;
Coord1BeltOriginRef.X := refX - TO_LREAL(refPulse) * Coord1PulseToMm;
Coord1BeltOriginRef.Y := refY;
Coord1BeltOriginRef.Z := refZ;
Coord1BeltOriginRef.A := 0.0;
Coord1BeltOriginRef.B := 0.0;
Coord1BeltOriginRef.C := 0.0;

GVL.Coord1RefPulse  := refPulse;
GVL.Coord1RefXyz[0] := refX;
GVL.Coord1RefXyz[1] := refY;
GVL.Coord1RefXyz[2] := refZ;
GVL.Coord1Scale[0]  := scaleX;
GVL.Coord1Scale[1]  := 0.0;
GVL.Coord1Scale[2]  := 0.0;

// Window budget. Explicitly writing the -1 sentinel on the no-window
// path also fixes a latent leak: a plain COORD1_UNBIND did not reset
// ExitPulse, so a SYS bind after a fly bind+unbind used to inherit the
// stale window and could window-exit spuriously.
IF exitPulseOffset > 0 THEN
    GVL.Coord1ExitPulse := refPulse + exitPulseOffset;
ELSE
    GVL.Coord1ExitPulse := -1;
END_IF

GVL.Coord1BindEventId := eventId;
GVL.Coord1CamMode     := GVL.COORD1_MODE_LINEAR;
GVL.Coord1CamCount    := 0;
GVL.Coord1Bound       := TRUE;

Coord1CommitBind := TRUE;

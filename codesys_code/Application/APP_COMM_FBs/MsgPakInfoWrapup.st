FUNCTION MsgPakInfoWrapup : BOOL
VAR_IN_OUT
	msgpak:MsgPakInfo;
END_VAR
VAR
	mpPacker: FB_MpPacker;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Stamp a global monotonic seq on every emitted packet. This is the one
// chokepoint every reply and event flows through, so injecting here means
// callers don't have to remember to bump ele_count or pack the field. UI
// uses seq to detect ring-drop gaps that per-request id-tracking can't see
// (unsolicited events have no `id`). Done before the ele_count>15 check so
// that check correctly accounts for the auto-added field.
GVL.ReMpSeqCounter := GVL.ReMpSeqCounter + 1;
// 'seq' + LINT need at most 4 + 9 bytes; the local packer has no bound
// slot, so check here before writing (2026-10-04).
IF (msgpak.whead - msgpak.buf) + 13 > TO_DINT(msgpak.buf_capacity) THEN
	msgpak.overflowed := TRUE;
ELSE
	msgpak.whead := mpPacker.PackString(pStart := msgpak.whead, psValue := ADR('seq'));
	msgpak.whead := mpPacker.PackLINT(pStart := msgpak.whead, liValue := TO_LINT(GVL.ReMpSeqCounter));
	msgpak.ele_count := msgpak.ele_count + 1;
END_IF

IF msgpak.useLargeElementCountField=FALSE AND msgpak.ele_count>15 THEN//the size field does not fit
	MsgPakInfoWrapup:=FALSE;
	//TODO: it's possible to move buffer around
	RETURN;
END_IF

// Overflow: since 2026-10-04 the FB_MpPacker primitives check the bound
// slot before writing and set msgpak.overflowed instead of writing past
// buf; a packet with a missing field is incomplete, so refuse it and bump
// the counter (visible in DiagPanel). The whead check stays for writers
// that bypass the bound slot.
IF msgpak.overflowed OR ((msgpak.whead - msgpak.buf) > TO_DINT(msgpak.buf_capacity)) THEN
	GVL.ReMpOverflowDropCount := GVL.ReMpOverflowDropCount + 1;
	MsgPakInfoWrapup := FALSE;
	RETURN;
END_IF



mpPacker.PackMapHeader(pStart := msgpak.buf, uiCount := TO_UINT(msgpak.ele_count), bForceLargeField := msgpak.useLargeElementCountField);

MsgPakInfoWrapup:=TRUE;

msgpak.buf_size:=TO_UDINT(msgpak.whead-msgpak.buf);
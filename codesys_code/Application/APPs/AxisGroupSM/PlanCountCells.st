METHOD PlanCountCells : BOOL
VAR_INPUT
    n    : DINT;    // cells the reel has just passed
    kind : INT;     // 1 packed, 2 left empty, 0 not said
END_VAR
VAR
    idx    : INT;
    segAt  : UDINT;
    left   : UDINT;
    segIdx : INT;
END_VAR
(* =========== IMPLEMENTATION =========== *)
// Count n tape cells into the retained plan (GVL.PlanCellsDone, packed /
// empty apart) and check them against the segment the plan puts at
// PlanCellsDone. Returns TRUE when the kind or the size disagrees; the
// cells are counted either way -- the tape has moved.
PlanCountCells := FALSE;
IF n <= 0 THEN
    RETURN;
END_IF;
segIdx := -1;
segAt := 0;
left := 0;
FOR idx := 0 TO MIN(GVL.PlanSegCount, 32) - 1 DO
    IF GVL.PlanCellsDone < segAt + DINT_TO_UDINT(ABS(GVL.PlanSeg[idx])) THEN
        segIdx := idx;
        left := segAt + DINT_TO_UDINT(ABS(GVL.PlanSeg[idx])) - GVL.PlanCellsDone;
        EXIT;
    END_IF;
    segAt := segAt + DINT_TO_UDINT(ABS(GVL.PlanSeg[idx]));
END_FOR;
IF kind > 0 THEN
    IF (segIdx < 0)
       OR ((kind = 1) <> (GVL.PlanSeg[MAX(segIdx, 0)] > 0))
       OR (DINT_TO_UDINT(n) > left) THEN
        PlanCountCells := TRUE;
        GVL.PlanMismatchCount := GVL.PlanMismatchCount + 1;
    END_IF;
END_IF;
IF kind = 1 THEN
    GVL.PlanCellsPacked := GVL.PlanCellsPacked + DINT_TO_UDINT(n);
ELSIF kind = 2 THEN
    GVL.PlanCellsEmpty := GVL.PlanCellsEmpty + DINT_TO_UDINT(n);
END_IF;
GVL.PlanCellsDone := GVL.PlanCellsDone + DINT_TO_UDINT(n);
GVL.PlanRev := GVL.PlanRev + 1;

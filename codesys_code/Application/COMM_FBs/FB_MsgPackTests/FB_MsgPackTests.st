(*
    FB_MsgPackTests
    Self-contained round-trip + regression tests for the MsgPack lib.
    Bypasses the web UI -- flip bRun to TRUE once; results appear on
    outputs. Several tests are *expected to fail* on today's code --
    they are the regression tests for the findings in doc/msgpack.md.

    Intended call site: PLC_PRG cyclically invokes this FB with
    bRun := GVL.bRunMsgPackTests. Operator toggles that bool in online
    mode, reads uiPassed / uiFailed / sFirstFail / sSummary.
*)
FUNCTION_BLOCK FB_MsgPackTests
VAR_INPUT
    bRun : BOOL;
END_VAR
VAR_OUTPUT
    bDone      : BOOL;
    uiPassed   : UINT;
    uiFailed   : UINT;
    sFirstFail : STRING(250);
    sSummary   : STRING(80);
END_VAR
VAR
    rt  : R_TRIG;
    buf : ARRAY[0..2047] OF BYTE;
    upk : FB_MpUnpacker;
END_VAR
(* =========== IMPLEMENTATION =========== *)
rt(CLK := bRun);
IF rt.Q THEN
    RunAll();
END_IF;

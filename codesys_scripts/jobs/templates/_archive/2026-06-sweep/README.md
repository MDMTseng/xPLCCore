# 2026-06-17 sweep -- one-shot scripts moved out of templates/

47 scripts moved here so `templates/` only holds the ~15 jobs that
are still part of an active workflow (push pipeline, virtual-motors
gate, lifecycle ops, msgpack self-test harness).

Categories of what got archived:

- **probe_\*** -- one-shot CODESYS scripting probes used to learn what
  fields/methods exist on a particular FB or device. Each one served a
  single investigation; reading code is the right reference now, not
  re-running these.
- **edit_\*** -- one-shot in-place ST edits already merged. Re-running
  them against the current code would either no-op or duplicate the
  edit (most do `replace` with a literal that's no longer there).
- **axis_diag\* / axis_status / dump_\* / online_read\* /
  online_discover** -- ad-hoc inspection wrappers. The SYS/GET_DIAG and
  SYS/GET_MACHINE_STATE replies now cover every counter and field
  these used to dump; use TCP, not these scripts.
- **b1_read_dbg / b1_setcoord_diag / m4_crash_diag / diag_roundtrip**
  -- closed-incident diagnostics. The PLC fixes that came out of these
  are in git history; the scripts themselves don't need to live in the
  active toolbox.
- **verify_axes_errid / unforce_trigger / delete_swap_real /
  delete_dead_pous / create_msgpack_tests_pou /
  ensure_axisgroupsm_actions / find_reelpullmotor** -- one-time
  build/cleanup operations that already ran.

Promoted back to `templates/` if needed: copy out, don't reference
this path from anything that lives long-term. Treat as read-only
history (same rule as the parent `_archive/`).

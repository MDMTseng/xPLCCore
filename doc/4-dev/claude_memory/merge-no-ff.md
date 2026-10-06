---
name: merge-no-ff
description: "Merging a work branch into main - the owner wants --no-ff (a merge commit), not a fast-forward; ask before pushing main"
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-06T02:07:52.364Z
---

When merging a work branch (e.g. flow-analysis) into main, use
`git merge --no-ff` so the branch shows as one merge commit. Before pushing
main, state the merge style in one line.

**Why:** 2026-10-06 the owner said "並進master"; I fast-forwarded main to
flow-analysis and pushed (4cd5de5 -> 944826b); the owner then said "no ff".
Rewriting main would have needed a force push, so the owner let it stand
("算了 就這樣吧").

**How to apply:** the repo's default branch is `main` (no master). Merge
with `--no-ff`, run the pre-push check with node on PATH
([[git-push-node-path]]), never force-push main without asking.

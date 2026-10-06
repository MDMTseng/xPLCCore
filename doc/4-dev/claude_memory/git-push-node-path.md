---
name: git-push-node-path
description: "xPLCCore push - the husky pre-push hook runs `npm run check` (tsc + vitest); Git Bash has no node on PATH, so prefix PATH=\"/c/Program Files/nodejs:$PATH\"; work branch is flow-analysis; open work list doc_review/TODO.md"
metadata:
  node_type: memory
  type: reference
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-10-06T01:42:24.563Z
---

- Repo xPLCCore, remote origin https://github.com/MDMTseng/xPLCCore.git, work
  branch `flow-analysis` (not main). The owner asked for commit + push on
  2026-10-06.
- `.husky/pre-push` runs `npm run check` (typecheck + vitest). In the Bash
  tool node is not on PATH (hook fails with code 127): run
  `export PATH="/c/Program Files/nodejs:$PATH"` first. Do not use
  `--no-verify`. Same for tsc / vitest: `node node_modules/typescript/bin/tsc`,
  `node node_modules/vitest/vitest.mjs run`.
- The open work is tracked in `doc_review/TODO.md` (tick items off there with
  the commit). Related: [[plc-architecture-review-2026-10-04]].

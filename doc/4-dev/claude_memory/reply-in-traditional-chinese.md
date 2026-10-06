---
name: reply-in-traditional-chinese
description: Talk to the user in Traditional Chinese; docs, commits and code comments stay English
metadata:
  node_type: memory
  type: feedback
  originSessionId: f66f7e0c-0330-4b23-97fe-13374412ecbb
  modified: 2026-09-23T16:21:59.694Z
---

Reply to this user in Traditional Chinese (繁體中文), not English. Requested on 2026-09-23 and repeated several times on 2026-09-24 ("記得用中文") -- it matters to them.

Scope clarified 2026-09-24: **only the conversation**. Repo documentation, git commit messages and code comments stay in English. (A Chinese section in doc/1-concepts/machine.md, "作業流程", was written in Chinese before this was clarified; it can stay.)

**Why:** Stated directly as a standing preference. The machine's system locale is also Chinese.

**How to apply:** Write prose, explanations, summaries and progress updates to the user in Traditional Chinese, Taiwan-style technical vocabulary. Keep technical identifiers in their original form -- product names, file paths, code, variable names, CLI flags, log output. Everything written into the repo stays English.

Refined 2026-09-25: "以後用中文回覆 過程用英文" means the user-visible text is Chinese and only the internal reasoning/thinking is English. After some mid-task status lines came out in English, the user reminded "記得用中文回應". So **every** user-visible line, including short progress notes between tool calls, is in Traditional Chinese. Tool descriptions and repo content stay English.

Repeated again 2026-10-03, several times ("中文", "用中文", "以後統一用中文 不要用英文了") after replies drifted into English during long soak monitoring -- especially after context compaction and in short status notes. Before sending ANY message, check it is Chinese; this includes one-line notification acknowledgements and the text between tool calls.

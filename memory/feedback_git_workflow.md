---
name: feedback-git-workflow
description: 用户不希望每次代码改动后自动提交到 GitHub
metadata:
  type: feedback
---

不要在每次代码改动后自动执行 git commit 和 git push。
只有用户明确说"提交"或"commit"时才执行。

**Why:** 用户希望自己控制提交节奏，不需要每个小改动都产生一条 commit。

**How to apply:** 改完代码验证没问题后直接结束，不附加任何 git 操作。

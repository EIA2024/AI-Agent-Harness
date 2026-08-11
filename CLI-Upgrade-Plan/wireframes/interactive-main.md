# Wireframe — Interactive Main

```text
 Personal AI   AI-Agent-Harness · session: CLI redesign · GLM-4.7 · connected

 You
 Design the CLI so approvals are easier to understand.

 · Reviewing the current approval flow

 ✓ filesystem.read  apps/api/routers/approvals.py · 142 lines · 21ms

 Agent
 The backend already supports approve, reject, and edit. I can expose all
 three in the CLI without changing the policy engine.

 ! Approval required · R2 local write
   filesystem.write
   Target: docs/cli.md
   Change: create a new CLI reference

   [A] Approve once  [E] Edit arguments  [R] Reject  [C] Cancel run


 > _
 Ctrl+P commands · Ctrl+R sessions · Ctrl+C cancel
```

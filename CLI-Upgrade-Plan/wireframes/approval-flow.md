# Wireframe — Approval Flow

```text
! Approval required · R3 external side effect

Tool
  mail.send

Action
  Send an email to alice@example.com

Arguments
  recipient   alice@example.com
  subject     "Status update"
  body        842 chars

Data boundary
  Content will be sent to an external service.

[A] Approve once
[E] Edit arguments
[R] Reject this action
[C] Cancel entire run
[O] Full details
```

Edit:
```text
Edit tool arguments
recipient  [alice@example.com                    ]
subject    [Status update                        ]
body       [ ...                                 ]

Ctrl+Enter submit edits · Esc cancel
```

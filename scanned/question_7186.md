# Q7186: rebroadcast replay drift in pending::PendingBlocksPool

## Question
Can an unprivileged attacker resend a previously seen transaction through normal retry behavior that reaches `chain/chain/src/pending.rs::PendingBlocksPool` with control over timing, equivalent encodings, and mempool replacement pressure and make nearcore treat rebroadcasted work as fresh authorization after the canonical state has moved on, breaking the invariant that rebroadcast must never revive authorization that the canonical state has already consumed or invalidated, and leading to unauthorized transaction?

## Target
- File/function: `chain/chain/src/pending.rs::PendingBlocksPool`
- Entrypoint: resend a previously seen transaction through normal retry behavior
- Attacker controls: timing, equivalent encodings, and mempool replacement pressure
- Exploit idea: treat rebroadcasted work as fresh authorization after the canonical state has moved on
- Invariant to test: rebroadcast must never revive authorization that the canonical state has already consumed or invalidated
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: write a rebroadcast-after-state-change test and assert stale work is rejected on every route

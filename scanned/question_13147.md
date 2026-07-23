# Q13147: pending queue resurrection in reed_solomon::decode

## Question
Can an unprivileged attacker submit transactions that become temporarily ineligible and later eligible again that reaches `core/primitives/src/reed_solomon.rs::decode` with control over nonce progression, balance changes, and retry timing within normal user flows and make nearcore resurrect work that should have been permanently rejected or superseded, breaking the invariant that once a transaction becomes invalid relative to the canonical state, it must not regain execution rights without fresh authorization, and leading to transaction manipulation?

## Target
- File/function: `core/primitives/src/reed_solomon.rs::decode`
- Entrypoint: submit transactions that become temporarily ineligible and later eligible again
- Attacker controls: nonce progression, balance changes, and retry timing within normal user flows
- Exploit idea: resurrect work that should have been permanently rejected or superseded
- Invariant to test: once a transaction becomes invalid relative to the canonical state, it must not regain execution rights without fresh authorization
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a pending-queue scenario with superseded transactions and assert obsolete work cannot re-enter execution

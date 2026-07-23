# Q13162: pending queue resurrection in v1::account_id_to_shard_id

## Question
Can an unprivileged attacker submit transactions that become temporarily ineligible and later eligible again that reaches `core/primitives/src/shard_layout/v1.rs::account_id_to_shard_id` with control over nonce progression, balance changes, and retry timing within normal user flows and make nearcore resurrect work that should have been permanently rejected or superseded, breaking the invariant that once a transaction becomes invalid relative to the canonical state, it must not regain execution rights without fresh authorization, and leading to transaction manipulation?

## Target
- File/function: `core/primitives/src/shard_layout/v1.rs::account_id_to_shard_id`
- Entrypoint: submit transactions that become temporarily ineligible and later eligible again
- Attacker controls: nonce progression, balance changes, and retry timing within normal user flows
- Exploit idea: resurrect work that should have been permanently rejected or superseded
- Invariant to test: once a transaction becomes invalid relative to the canonical state, it must not regain execution rights without fresh authorization
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a pending-queue scenario with superseded transactions and assert obsolete work cannot re-enter execution

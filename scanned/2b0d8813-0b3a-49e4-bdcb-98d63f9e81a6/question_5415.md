# Q5415: rollback misses one storage layer in receipt_to_tx::ancestor_heights_saturates_at_zero

## Question
Can an unprivileged attacker submit a transaction that mutates multiple storage abstractions before failing that reaches `chain/chain/src/receipt_to_tx.rs::ancestor_heights_saturates_at_zero` with control over writes that touch trie, flat-storage, and receipt metadata in one failing path and make nearcore revert one persistence layer but leave another layer advanced, breaking the invariant that every rejected transition must roll back all storage layers to the same prior root, and leading to consensus flaws?

## Target
- File/function: `chain/chain/src/receipt_to_tx.rs::ancestor_heights_saturates_at_zero`
- Entrypoint: submit a transaction that mutates multiple storage abstractions before failing
- Attacker controls: writes that touch trie, flat-storage, and receipt metadata in one failing path
- Exploit idea: revert one persistence layer but leave another layer advanced
- Invariant to test: every rejected transition must roll back all storage layers to the same prior root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a failing multi-layer update test and assert trie root, flat storage, and receipt metadata all revert together

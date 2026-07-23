# Q5416: rollback misses one storage layer in receipt_to_tx::center_out_basic

## Question
Can an unprivileged attacker submit a transaction that mutates multiple storage abstractions before failing that reaches `chain/chain/src/receipt_to_tx.rs::center_out_basic` with control over writes that touch trie, flat-storage, and receipt metadata in one failing path and make nearcore revert one persistence layer but leave another layer advanced, breaking the invariant that every rejected transition must roll back all storage layers to the same prior root, and leading to consensus flaws?

## Target
- File/function: `chain/chain/src/receipt_to_tx.rs::center_out_basic`
- Entrypoint: submit a transaction that mutates multiple storage abstractions before failing
- Attacker controls: writes that touch trie, flat-storage, and receipt metadata in one failing path
- Exploit idea: revert one persistence layer but leave another layer advanced
- Invariant to test: every rejected transition must roll back all storage layers to the same prior root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a failing multi-layer update test and assert trie root, flat storage, and receipt metadata all revert together

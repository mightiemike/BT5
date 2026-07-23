# Q6634: rollback misses one storage layer in parallel_loader::make_loading_plan

## Question
Can an unprivileged attacker submit a transaction that mutates multiple storage abstractions before failing that reaches `core/store/src/trie/mem/parallel_loader.rs::make_loading_plan` with control over writes that touch trie, flat-storage, and receipt metadata in one failing path and make nearcore revert one persistence layer but leave another layer advanced, breaking the invariant that every rejected transition must roll back all storage layers to the same prior root, and leading to consensus flaws?

## Target
- File/function: `core/store/src/trie/mem/parallel_loader.rs::make_loading_plan`
- Entrypoint: submit a transaction that mutates multiple storage abstractions before failing
- Attacker controls: writes that touch trie, flat-storage, and receipt metadata in one failing path
- Exploit idea: revert one persistence layer but leave another layer advanced
- Invariant to test: every rejected transition must roll back all storage layers to the same prior root
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a failing multi-layer update test and assert trie root, flat storage, and receipt metadata all revert together

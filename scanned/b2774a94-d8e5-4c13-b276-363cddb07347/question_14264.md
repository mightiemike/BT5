# Q14264: stale predecessor state use in block_processing_utils::remove_optimistic

## Question
Can an unprivileged attacker submit transactions that update the same state across sequential chunks or shard updates that reaches `chain/chain/src/block_processing_utils.rs::remove_optimistic` with control over cross-chunk dependencies and repeated writes to hot accounts or contracts and make nearcore apply later work against a stale predecessor snapshot instead of the current canonical one, breaking the invariant that every accepted transition must execute against the latest canonical predecessor state, and leading to consensus flaws?

## Target
- File/function: `chain/chain/src/block_processing_utils.rs::remove_optimistic`
- Entrypoint: submit transactions that update the same state across sequential chunks or shard updates
- Attacker controls: cross-chunk dependencies and repeated writes to hot accounts or contracts
- Exploit idea: apply later work against a stale predecessor snapshot instead of the current canonical one
- Invariant to test: every accepted transition must execute against the latest canonical predecessor state
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a sequential-chunk dependency test and assert later chunks always read the predecessor state committed by earlier ones

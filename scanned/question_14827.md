# Q14827: stale predecessor state use in congestion_info::outgoing_congestion

## Question
Can an unprivileged attacker submit transactions that update the same state across sequential chunks or shard updates that reaches `core/primitives/src/congestion_info.rs::outgoing_congestion` with control over cross-chunk dependencies and repeated writes to hot accounts or contracts and make nearcore apply later work against a stale predecessor snapshot instead of the current canonical one, breaking the invariant that every accepted transition must execute against the latest canonical predecessor state, and leading to consensus flaws?

## Target
- File/function: `core/primitives/src/congestion_info.rs::outgoing_congestion`
- Entrypoint: submit transactions that update the same state across sequential chunks or shard updates
- Attacker controls: cross-chunk dependencies and repeated writes to hot accounts or contracts
- Exploit idea: apply later work against a stale predecessor snapshot instead of the current canonical one
- Invariant to test: every accepted transition must execute against the latest canonical predecessor state
- Expected Immunefi impact: Consensus flaws
- Fast validation: write a sequential-chunk dependency test and assert later chunks always read the predecessor state committed by earlier ones

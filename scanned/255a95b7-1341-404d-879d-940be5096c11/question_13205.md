# Q13205: receipt metadata replay in shard_chunk_header_inner::prev_state_root

## Question
Can an unprivileged attacker submit transactions that generate many receipts and callbacks that reaches `core/primitives/src/sharding/shard_chunk_header_inner.rs::prev_state_root` with control over receipt ids, callback trees, and retry timing reachable from contract execution and make nearcore reuse or resurrect metadata for a receipt that should already be consumed, breaking the invariant that each receipt identifier and routing record must transition monotonically from pending to consumed once, and leading to transaction manipulation?

## Target
- File/function: `core/primitives/src/sharding/shard_chunk_header_inner.rs::prev_state_root`
- Entrypoint: submit transactions that generate many receipts and callbacks
- Attacker controls: receipt ids, callback trees, and retry timing reachable from contract execution
- Exploit idea: reuse or resurrect metadata for a receipt that should already be consumed
- Invariant to test: each receipt identifier and routing record must transition monotonically from pending to consumed once
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a receipt-consumption test that retries the same logical callback path and assert consumed metadata cannot be reused

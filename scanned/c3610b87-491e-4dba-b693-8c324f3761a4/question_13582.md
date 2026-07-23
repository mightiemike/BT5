# Q13582: receipt metadata replay in from_flat::construct_trie_from_flat

## Question
Can an unprivileged attacker submit transactions that generate many receipts and callbacks that reaches `core/store/src/trie/from_flat.rs::construct_trie_from_flat` with control over receipt ids, callback trees, and retry timing reachable from contract execution and make nearcore reuse or resurrect metadata for a receipt that should already be consumed, breaking the invariant that each receipt identifier and routing record must transition monotonically from pending to consumed once, and leading to transaction manipulation?

## Target
- File/function: `core/store/src/trie/from_flat.rs::construct_trie_from_flat`
- Entrypoint: submit transactions that generate many receipts and callbacks
- Attacker controls: receipt ids, callback trees, and retry timing reachable from contract execution
- Exploit idea: reuse or resurrect metadata for a receipt that should already be consumed
- Invariant to test: each receipt identifier and routing record must transition monotonically from pending to consumed once
- Expected Immunefi impact: Transaction manipulation
- Fast validation: write a receipt-consumption test that retries the same logical callback path and assert consumed metadata cannot be reused

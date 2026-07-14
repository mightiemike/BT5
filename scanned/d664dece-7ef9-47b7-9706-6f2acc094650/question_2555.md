# Q2555: undo state cache PathBuilder left/right depth boundary via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `undo_state` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `undo_state`, using a crafted PathBuilder left/right depth boundary input and the pre-eval callback enabled versus disabled validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cache keys must represent exact tree identity/content and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/tree_cache.rs::undo_state
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `undo_state`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cache keys must represent exact tree identity/content
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

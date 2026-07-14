# Q1421: undo state cache PathBuilder left/right depth boundary via stream hash versus tree hash

## Question
Can an unprivileged attacker reach `undo_state` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `undo_state`, using a crafted PathBuilder left/right depth boundary input and the stream hash versus tree hash validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that restore/undo must remove future state and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/tree_cache.rs::undo_state
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `undo_state`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through stream hash versus tree hash, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

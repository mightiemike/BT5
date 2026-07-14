# Q3941: pop cache PathBuilder left/right depth boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `pop` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `pop`, using a crafted PathBuilder left/right depth boundary input and the node_from_stream versus node_from_bytes validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that restore/undo must remove future state and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/tree_cache.rs::pop
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `pop`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

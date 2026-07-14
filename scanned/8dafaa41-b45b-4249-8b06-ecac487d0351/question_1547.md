# Q1547: update cache PathBuilder left/right depth boundary via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `update` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `update`, using a crafted PathBuilder left/right depth boundary input and the cost limit at exact operator boundary validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cache keys must represent exact tree identity/content and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/tree_cache.rs::update
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `update`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cache keys must represent exact tree identity/content
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

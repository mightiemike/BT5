# Q2681: update cache PathBuilder left/right depth boundary via strict canonical rejection versus successful round trip

## Question
Can an unprivileged attacker reach `update` in `src/serde/tree_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `update`, using a crafted PathBuilder left/right depth boundary input and the strict canonical rejection versus successful round trip validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that restore/undo must remove future state and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/tree_cache.rs::update
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `update`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through strict canonical rejection versus successful round trip, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

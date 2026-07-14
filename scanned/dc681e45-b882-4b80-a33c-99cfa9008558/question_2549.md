# Q2549: done cache PathBuilder left/right depth boundary via deserialize then serialized_length

## Question
Can an unprivileged attacker reach `done` in `src/serde/path_builder.rs` through public cache-backed serialization, hashing, interning, or incremental state through `done`, using a crafted PathBuilder left/right depth boundary input and the deserialize then serialized_length validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that cache keys must represent exact tree identity/content and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/path_builder.rs::done
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `done`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through deserialize then serialized_length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cache keys must represent exact tree identity/content
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

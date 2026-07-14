# Q1285: intern tree cache ObjectCache key collision candidate via writer limit at exact output length

## Question
Can an unprivileged attacker reach `intern_tree` in `src/serde/intern.rs` through public cache-backed serialization, hashing, interning, or incremental state through `intern_tree`, using a crafted ObjectCache key collision candidate input and the writer limit at exact output length validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that cache keys must represent exact tree identity/content and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/intern.rs::intern_tree
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `intern_tree`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for ObjectCache key collision candidate, drive it through writer limit at exact output length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cache keys must represent exact tree identity/content
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1971: visit cache incremental serializer undo via deserialize then serialized_length

## Question
Can an unprivileged attacker reach `visit` in `src/serde/bitset.rs` through public cache-backed serialization, hashing, interning, or incremental state through `visit`, using a crafted incremental serializer undo input and the deserialize then serialized_length validation path while controlling equal-content atoms at different allocation identities, so the code returning stale hash, length, path, or bytes for another tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that interning must preserve tree hash and pair order and causing Critical tree identity corruption: stale cache returns wrong tree/hash?

## Target
- File/function: src/serde/bitset.rs::visit
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `visit`
- Attacker controls: equal-content atoms at different allocation identities
- Exploit idea: Build the smallest CLVM blob/program/API call for incremental serializer undo, drive it through deserialize then serialized_length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: interning must preserve tree hash and pair order
- Expected Immunefi impact: Critical tree identity corruption: stale cache returns wrong tree/hash
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

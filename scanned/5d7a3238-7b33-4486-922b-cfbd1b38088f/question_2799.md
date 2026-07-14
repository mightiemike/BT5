# Q2799: calculate depth simple cache incremental serializer undo via execute then serialize legacy

## Question
Can an unprivileged attacker reach `calculate_depth_simple` in `src/serde/object_cache.rs` through public cache-backed serialization, hashing, interning, or incremental state through `calculate_depth_simple`, using a crafted incremental serializer undo input and the execute then serialize legacy validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cache keys must represent exact tree identity/content and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/object_cache.rs::calculate_depth_simple
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `calculate_depth_simple`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for incremental serializer undo, drive it through execute then serialize legacy, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cache keys must represent exact tree identity/content
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

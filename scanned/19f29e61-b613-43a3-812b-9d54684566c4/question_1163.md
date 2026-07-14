# Q1163: len cache PathBuilder left/right depth boundary via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `len` in `src/serde/path_builder.rs` through public cache-backed serialization, hashing, interning, or incremental state through `len`, using a crafted PathBuilder left/right depth boundary input and the strict mode versus non-strict mode where exposed validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that restore/undo must remove future state and causing Critical canonical serialization failure: cached path/bytes encode wrong tree?

## Target
- File/function: src/serde/path_builder.rs::len
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `len`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: Critical canonical serialization failure: cached path/bytes encode wrong tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

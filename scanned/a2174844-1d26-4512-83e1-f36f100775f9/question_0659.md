# Q659: done cache PathBuilder left/right depth boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `done` in `src/serde/path_builder.rs` through public cache-backed serialization, hashing, interning, or incremental state through `done`, using a crafted PathBuilder left/right depth boundary input and the node_from_stream versus node_from_bytes validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that restore/undo must remove future state and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/path_builder.rs::done
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `done`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for PathBuilder left/right depth boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: restore/undo must remove future state
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

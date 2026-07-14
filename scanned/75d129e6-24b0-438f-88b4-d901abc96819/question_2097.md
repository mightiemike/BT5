# Q2097: extend cache incremental serializer undo via tree cache checkpoint before and after restore

## Question
Can an unprivileged attacker reach `extend` in `src/serde/bitset.rs` through public cache-backed serialization, hashing, interning, or incremental state through `extend`, using a crafted incremental serializer undo input and the tree cache checkpoint before and after restore validation path while controlling restore/undo sequences, so the code keeping restored state reachable to later input, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cached length/hash/path must match uncached computation and causing High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths?

## Target
- File/function: src/serde/bitset.rs::extend
- Entrypoint: public cache-backed serialization, hashing, interning, or incremental state through `extend`
- Attacker controls: restore/undo sequences
- Exploit idea: Build the smallest CLVM blob/program/API call for incremental serializer undo, drive it through tree cache checkpoint before and after restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cached length/hash/path must match uncached computation
- Expected Immunefi impact: High stale cache error: repeated inputs return wrong nodes, lengths, hashes, or paths
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

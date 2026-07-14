# Q2287: hash blob core empty atom versus nil boundary via round trip through tree hash and bytes

## Question
Can an unprivileged attacker reach `hash_blob` in `src/serde/bytes32.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `hash_blob`, using a crafted empty atom versus nil boundary input and the round trip through tree hash and bytes validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/serde/bytes32.rs::hash_blob
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `hash_blob`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through round trip through tree hash and bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

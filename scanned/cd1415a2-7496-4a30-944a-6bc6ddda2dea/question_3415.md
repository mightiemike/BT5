# Q3415: i32 atom core empty atom versus nil boundary via allocator debug semantics versus release semantics

## Question
Can an unprivileged attacker reach `i32_atom` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `i32_atom`, using a crafted empty atom versus nil boundary input and the allocator debug semantics versus release semantics validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/op_utils.rs::i32_atom
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `i32_atom`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through allocator debug semantics versus release semantics, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

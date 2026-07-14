# Q883: restore checkpoint core empty atom versus nil boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `restore_checkpoint` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `restore_checkpoint`, using a crafted empty atom versus nil boundary input and the node_from_stream versus node_from_bytes validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/allocator.rs::restore_checkpoint
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `restore_checkpoint`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

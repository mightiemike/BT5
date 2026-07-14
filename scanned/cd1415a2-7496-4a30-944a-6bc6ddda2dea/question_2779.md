# Q2779: node ptr core empty atom versus nil boundary via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `node_ptr` in `src/error.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `node_ptr`, using a crafted empty atom versus nil boundary input and the pre-eval callback enabled versus disabled validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that tree hash must use exact atom bytes and pair order and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/error.rs::node_ptr
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `node_ptr`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

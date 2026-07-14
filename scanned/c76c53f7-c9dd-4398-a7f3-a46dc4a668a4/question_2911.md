# Q2911: new atom and cost core empty atom versus nil boundary via execute then serialize legacy

## Question
Can an unprivileged attacker reach `new_atom_and_cost` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_atom_and_cost`, using a crafted empty atom versus nil boundary input and the execute then serialize legacy validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/op_utils.rs::new_atom_and_cost
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_atom_and_cost`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through execute then serialize legacy, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

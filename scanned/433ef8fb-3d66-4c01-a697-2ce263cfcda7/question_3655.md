# Q3655: add ghost pair core empty atom versus nil boundary via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `add_ghost_pair` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `add_ghost_pair`, using a crafted empty atom versus nil boundary input and the nil atom reused inside pair validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/allocator.rs::add_ghost_pair
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `add_ghost_pair`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1387: new i64 core empty atom versus nil boundary via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `new_i64` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_i64`, using a crafted empty atom versus nil boundary input and the strict mode versus non-strict mode where exposed validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/allocator.rs::new_i64
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_i64`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

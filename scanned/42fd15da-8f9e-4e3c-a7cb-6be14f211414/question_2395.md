# Q2395: fits in small atom core empty atom versus nil boundary via default flags versus MEMPOOL_MODE

## Question
Can an unprivileged attacker reach `fits_in_small_atom` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `fits_in_small_atom`, using a crafted empty atom versus nil boundary input and the default flags versus MEMPOOL_MODE validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that integer helpers must agree with operator semantics and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/allocator.rs::fits_in_small_atom
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `fits_in_small_atom`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through default flags versus MEMPOOL_MODE, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

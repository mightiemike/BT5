# Q3857: reduction core allocator restore after pair creation via mempool mode followed by block mode replay

## Question
Can an unprivileged attacker reach `reduction` in `src/reduction.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `reduction`, using a crafted allocator restore after pair creation input and the mempool mode followed by block mode replay validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that valid NodePtr type/identity must remain stable and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/reduction.rs::reduction
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `reduction`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for allocator restore after pair creation, drive it through mempool mode followed by block mode replay, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

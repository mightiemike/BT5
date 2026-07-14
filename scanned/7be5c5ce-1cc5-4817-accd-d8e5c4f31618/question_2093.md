# Q2093: reduction core allocator restore after pair creation via Program.run_with_cost versus run_serialized_chia_program

## Question
Can an unprivileged attacker reach `reduction` in `src/reduction.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `reduction`, using a crafted allocator restore after pair creation input and the Program.run_with_cost versus run_serialized_chia_program validation path while controlling integer atoms at sign and length boundaries, so the code parsing numeric bytes differently than operators, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that valid NodePtr type/identity must remain stable and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/reduction.rs::reduction
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `reduction`
- Attacker controls: integer atoms at sign and length boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for allocator restore after pair creation, drive it through Program.run_with_cost versus run_serialized_chia_program, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q515: op lognot operator pair supplied where atom is required via same tree allocated twice in distinct allocators

## Question
Can an unprivileged attacker reach `op_lognot` in `src/more_ops.rs` through public CLVM execution through `op_lognot` invoked by run_program or run_serialized_chia_program, using a crafted pair supplied where atom is required input and the same tree allocated twice in distinct allocators validation path while controlling large positive, negative, sign-bit, and zero atoms, so the code normalizing integer bytes differently across execution paths, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator result, error, and cost must match Chia CLVM semantics and causing Critical consensus divergence: operator output differs for same spend?

## Target
- File/function: src/more_ops.rs::op_lognot
- Entrypoint: public CLVM execution through `op_lognot` invoked by run_program or run_serialized_chia_program
- Attacker controls: large positive, negative, sign-bit, and zero atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for pair supplied where atom is required, drive it through same tree allocated twice in distinct allocators, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator result, error, and cost must match Chia CLVM semantics
- Expected Immunefi impact: Critical consensus divergence: operator output differs for same spend
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

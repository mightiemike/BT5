# Q3539: op lsh operator pair supplied where atom is required via strict canonical rejection versus successful round trip

## Question
Can an unprivileged attacker reach `op_lsh` in `src/more_ops.rs` through public CLVM execution through `op_lsh` invoked by run_program or run_serialized_chia_program, using a crafted pair supplied where atom is required input and the strict canonical rejection versus successful round trip validation path while controlling large positive, negative, sign-bit, and zero atoms, so the code normalizing integer bytes differently across execution paths, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator result, error, and cost must match Chia CLVM semantics and causing Critical consensus divergence: operator output differs for same spend?

## Target
- File/function: src/more_ops.rs::op_lsh
- Entrypoint: public CLVM execution through `op_lsh` invoked by run_program or run_serialized_chia_program
- Attacker controls: large positive, negative, sign-bit, and zero atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for pair supplied where atom is required, drive it through strict canonical rejection versus successful round trip, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator result, error, and cost must match Chia CLVM semantics
- Expected Immunefi impact: Critical consensus divergence: operator output differs for same spend
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q11: op strlen operator pair supplied where atom is required via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `op_strlen` in `src/more_ops.rs` through public CLVM execution through `op_strlen` invoked by run_program or run_serialized_chia_program, using a crafted pair supplied where atom is required input and the fresh allocator versus checkpoint restore validation path while controlling large positive, negative, sign-bit, and zero atoms, so the code normalizing integer bytes differently across execution paths, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator result, error, and cost must match Chia CLVM semantics and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/more_ops.rs::op_strlen
- Entrypoint: public CLVM execution through `op_strlen` invoked by run_program or run_serialized_chia_program
- Attacker controls: large positive, negative, sign-bit, and zero atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for pair supplied where atom is required, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator result, error, and cost must match Chia CLVM semantics
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

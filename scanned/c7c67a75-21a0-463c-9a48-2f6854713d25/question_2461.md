# Q2461: op if operator negative-zero-like atom via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `op_if` in `src/core_ops.rs` through public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program, using a crafted negative-zero-like atom input and the cost limit at exact operator boundary validation path while controlling large positive, negative, sign-bit, and zero atoms, so the code normalizing integer bytes differently across execution paths, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that invalid atoms or pairs must reject before output and causing Critical consensus divergence: operator output differs for same spend?

## Target
- File/function: src/core_ops.rs::op_if
- Entrypoint: public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program
- Attacker controls: large positive, negative, sign-bit, and zero atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for negative-zero-like atom, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: invalid atoms or pairs must reject before output
- Expected Immunefi impact: Critical consensus divergence: operator output differs for same spend
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q191: op bls g2 subtract crypto oversized message cost boundary via Program.run_with_cost versus run_serialized_chia_program

## Question
Can an unprivileged attacker reach `op_bls_g2_subtract` in `src/bls_ops.rs` through public CLVM execution through `op_bls_g2_subtract` invoked by a spend using crypto/hash opcodes, using a crafted oversized message cost boundary input and the Program.run_with_cost versus run_serialized_chia_program validation path while controlling malformed keys, signatures, messages, points, or hashes, so the code accepting invalid proof material or rejecting valid material versus Chia semantics, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that valid Chia-compatible inputs must not diverge across APIs and causing Critical consensus divergence: valid crypto material is accepted by one path and rejected by another?

## Target
- File/function: src/bls_ops.rs::op_bls_g2_subtract
- Entrypoint: public CLVM execution through `op_bls_g2_subtract` invoked by a spend using crypto/hash opcodes
- Attacker controls: malformed keys, signatures, messages, points, or hashes
- Exploit idea: Build the smallest CLVM blob/program/API call for oversized message cost boundary, drive it through Program.run_with_cost versus run_serialized_chia_program, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid Chia-compatible inputs must not diverge across APIs
- Expected Immunefi impact: Critical consensus divergence: valid crypto material is accepted by one path and rejected by another
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

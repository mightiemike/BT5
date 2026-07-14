# Q1460: op mod operator i64/u64 fast-path boundary via node_to_bytes versus node_to_bytes_limit

## Question
Can an unprivileged attacker reach `op_mod` in `src/more_ops.rs` through public CLVM execution through `op_mod` invoked by run_program or run_serialized_chia_program, using a crafted i64/u64 fast-path boundary input and the node_to_bytes versus node_to_bytes_limit validation path while controlling minimal and non-minimal integer atoms, so the code accepting invalid argument shape before failing, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cost must include all processed attacker bytes and causing Critical consensus divergence: operator output differs for same spend?

## Target
- File/function: src/more_ops.rs::op_mod
- Entrypoint: public CLVM execution through `op_mod` invoked by run_program or run_serialized_chia_program
- Attacker controls: minimal and non-minimal integer atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for i64/u64 fast-path boundary, drive it through node_to_bytes versus node_to_bytes_limit, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost must include all processed attacker bytes
- Expected Immunefi impact: Critical consensus divergence: operator output differs for same spend
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

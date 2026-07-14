# Q1138: op if operator shift amount word-size boundary via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `op_if` in `src/core_ops.rs` through public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program, using a crafted shift amount word-size boundary input and the pre-eval callback enabled versus disabled validation path while controlling minimal and non-minimal integer atoms, so the code accepting invalid argument shape before failing, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that operator result, error, and cost must match Chia CLVM semantics and causing Critical consensus divergence: operator output differs for same spend?

## Target
- File/function: src/core_ops.rs::op_if
- Entrypoint: public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program
- Attacker controls: minimal and non-minimal integer atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for shift amount word-size boundary, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator result, error, and cost must match Chia CLVM semantics
- Expected Immunefi impact: Critical consensus divergence: operator output differs for same spend
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

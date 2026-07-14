# Q3658: op eq operator shift amount word-size boundary via execute then serialize backrefs

## Question
Can an unprivileged attacker reach `op_eq` in `src/core_ops.rs` through public CLVM execution through `op_eq` invoked by run_program or run_serialized_chia_program, using a crafted shift amount word-size boundary input and the execute then serialize backrefs validation path while controlling minimal and non-minimal integer atoms, so the code accepting invalid argument shape before failing, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that operator result, error, and cost must match Chia CLVM semantics and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/core_ops.rs::op_eq
- Entrypoint: public CLVM execution through `op_eq` invoked by run_program or run_serialized_chia_program
- Attacker controls: minimal and non-minimal integer atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for shift amount word-size boundary, drive it through execute then serialize backrefs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator result, error, and cost must match Chia CLVM semantics
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q256: op if operator shift amount word-size boundary via malformed input followed by valid input reuse

## Question
Can an unprivileged attacker reach `op_if` in `src/core_ops.rs` through public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program, using a crafted shift amount word-size boundary input and the malformed input followed by valid input reuse validation path while controlling fast-path versus bignum-fallback inputs, so the code undercharging bytes that affect output, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that fast paths must equal generic bignum behavior and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/core_ops.rs::op_if
- Entrypoint: public CLVM execution through `op_if` invoked by run_program or run_serialized_chia_program
- Attacker controls: fast-path versus bignum-fallback inputs
- Exploit idea: Build the smallest CLVM blob/program/API call for shift amount word-size boundary, drive it through malformed input followed by valid input reuse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: fast paths must equal generic bignum behavior
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

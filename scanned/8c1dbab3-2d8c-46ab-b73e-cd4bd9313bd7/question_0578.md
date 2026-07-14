# Q578: op not operator i64/u64 fast-path boundary via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `op_not` in `src/more_ops.rs` through public CLVM execution through `op_not` invoked by run_program or run_serialized_chia_program, using a crafted i64/u64 fast-path boundary input and the nil atom reused inside pair validation path while controlling fast-path versus bignum-fallback inputs, so the code undercharging bytes that affect output, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that invalid atoms or pairs must reject before output and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/more_ops.rs::op_not
- Entrypoint: public CLVM execution through `op_not` invoked by run_program or run_serialized_chia_program
- Attacker controls: fast-path versus bignum-fallback inputs
- Exploit idea: Build the smallest CLVM blob/program/API call for i64/u64 fast-path boundary, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: invalid atoms or pairs must reject before output
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

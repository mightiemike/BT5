# Q2776: op eq operator shift amount word-size boundary via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `op_eq` in `src/core_ops.rs` through public CLVM execution through `op_eq` invoked by run_program or run_serialized_chia_program, using a crafted shift amount word-size boundary input and the serde_2026 direct versus serde auto validation path while controlling fast-path versus bignum-fallback inputs, so the code undercharging bytes that affect output, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that fast paths must equal generic bignum behavior and causing High undercharged execution: operator input influences output below expected cost?

## Target
- File/function: src/core_ops.rs::op_eq
- Entrypoint: public CLVM execution through `op_eq` invoked by run_program or run_serialized_chia_program
- Attacker controls: fast-path versus bignum-fallback inputs
- Exploit idea: Build the smallest CLVM blob/program/API call for shift amount word-size boundary, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: fast paths must equal generic bignum behavior
- Expected Immunefi impact: High undercharged execution: operator input influences output below expected cost
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

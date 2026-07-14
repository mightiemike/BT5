# Q2652: allow unknown ops execution unknown opcode in MEMPOOL_MODE via stream hash versus tree hash

## Question
Can an unprivileged attacker reach `allow_unknown_ops` in `src/dialect.rs` through public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted unknown opcode in MEMPOOL_MODE input and the stream hash versus tree hash validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that operator availability must follow active dialect and softfork state and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/dialect.rs::allow_unknown_ops
- Entrypoint: public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for unknown opcode in MEMPOOL_MODE, drive it through stream hash versus tree hash, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

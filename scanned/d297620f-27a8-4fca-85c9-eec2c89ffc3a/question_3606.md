# Q3606: pop execution softfork guard followed by disabled opcode via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `pop` in `src/run_program.rs` through public CLVM execution through `pop` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted softfork guard followed by disabled opcode input and the strict mode versus non-strict mode where exposed validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that operator availability must follow active dialect and softfork state and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/run_program.rs::pop
- Entrypoint: public CLVM execution through `pop` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for softfork guard followed by disabled opcode, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

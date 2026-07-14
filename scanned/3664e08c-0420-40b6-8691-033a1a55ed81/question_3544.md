# Q3544: allow unknown ops execution cost one unit below required work via parse then execute

## Question
Can an unprivileged attacker reach `allow_unknown_ops` in `src/runtime_dialect.rs` through public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the parse then execute validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that cost and limit checks must precede consensus-visible output and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/runtime_dialect.rs::allow_unknown_ops
- Entrypoint: public CLVM execution through `allow_unknown_ops` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through parse then execute, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

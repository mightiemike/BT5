# Q2094: run program with counters execution cost one unit below required work via node_to_bytes versus node_to_bytes_limit

## Question
Can an unprivileged attacker reach `run_program_with_counters` in `src/run_program.rs` through public CLVM execution through `run_program_with_counters` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the node_to_bytes versus node_to_bytes_limit validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that operator availability must follow active dialect and softfork state and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/run_program.rs::run_program_with_counters
- Entrypoint: public CLVM execution through `run_program_with_counters` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through node_to_bytes versus node_to_bytes_limit, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

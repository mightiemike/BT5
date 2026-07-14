# Q494: Dialect execution cost one unit below required work via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `Dialect` in `wheel/python/clvm_rs/chia_dialect.py` through public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the read cache lookup before and after pop validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that same program/env/flags/max_cost must produce identical result, error, and cost and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: wheel/python/clvm_rs/chia_dialect.py::Dialect
- Entrypoint: public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: same program/env/flags/max_cost must produce identical result, error, and cost
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

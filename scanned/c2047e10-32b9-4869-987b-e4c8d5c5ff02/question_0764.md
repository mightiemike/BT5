# Q764: f lookup for hashmap execution cost one unit below required work via cost limit at exact operator boundary

## Question
Can an unprivileged attacker reach `f_lookup_for_hashmap` in `src/f_table.rs` through public CLVM execution through `f_lookup_for_hashmap` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the cost limit at exact operator boundary validation path while controlling caller-visible flags and max_cost, so the code accepting work before the cost failure is observable, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that cost and limit checks must precede consensus-visible output and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/f_table.rs::f_lookup_for_hashmap
- Entrypoint: public CLVM execution through `f_lookup_for_hashmap` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: caller-visible flags and max_cost
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through cost limit at exact operator boundary, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3534: gc candidate execution cost one unit below required work via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `gc_candidate` in `src/dialect.rs` through public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the read cache lookup before and after pop validation path while controlling unknown or reserved operator bytes, so the code enabling or disabling an operator under the wrong dialect state, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that same program/env/flags/max_cost must produce identical result, error, and cost and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/dialect.rs::gc_candidate
- Entrypoint: public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: unknown or reserved operator bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: same program/env/flags/max_cost must produce identical result, error, and cost
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

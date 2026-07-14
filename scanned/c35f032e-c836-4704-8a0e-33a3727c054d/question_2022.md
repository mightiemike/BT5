# Q2022: gc candidate execution unknown opcode in MEMPOOL_MODE via default flags versus MEMPOOL_MODE

## Question
Can an unprivileged attacker reach `gc_candidate` in `src/dialect.rs` through public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted unknown opcode in MEMPOOL_MODE input and the default flags versus MEMPOOL_MODE validation path while controlling unknown or reserved operator bytes, so the code enabling or disabling an operator under the wrong dialect state, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that same program/env/flags/max_cost must produce identical result, error, and cost and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/dialect.rs::gc_candidate
- Entrypoint: public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: unknown or reserved operator bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for unknown opcode in MEMPOOL_MODE, drive it through default flags versus MEMPOOL_MODE, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: same program/env/flags/max_cost must produce identical result, error, and cost
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

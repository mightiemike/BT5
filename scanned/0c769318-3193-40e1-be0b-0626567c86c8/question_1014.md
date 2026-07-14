# Q1014: gc candidate execution cost one unit below required work via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `gc_candidate` in `src/dialect.rs` through public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the strict mode versus non-strict mode where exposed validation path while controlling unknown or reserved operator bytes, so the code enabling or disabling an operator under the wrong dialect state, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that cost and limit checks must precede consensus-visible output and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/dialect.rs::gc_candidate
- Entrypoint: public CLVM execution through `gc_candidate` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: unknown or reserved operator bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

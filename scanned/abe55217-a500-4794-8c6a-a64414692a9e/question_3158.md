# Q3158: f lookup for hashmap execution apply/quote nesting around allocator restore via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `f_lookup_for_hashmap` in `src/f_table.rs` through public CLVM execution through `f_lookup_for_hashmap` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted apply/quote nesting around allocator restore input and the node_from_stream versus node_from_bytes validation path while controlling unknown or reserved operator bytes, so the code enabling or disabling an operator under the wrong dialect state, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that operator availability must follow active dialect and softfork state and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/f_table.rs::f_lookup_for_hashmap
- Entrypoint: public CLVM execution through `f_lookup_for_hashmap` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: unknown or reserved operator bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for apply/quote nesting around allocator restore, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

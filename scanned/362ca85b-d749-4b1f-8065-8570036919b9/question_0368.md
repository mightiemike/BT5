# Q368: Dialect execution apply/quote nesting around allocator restore via tree_hash before and after intern_tree

## Question
Can an unprivileged attacker reach `Dialect` in `wheel/python/clvm_rs/chia_dialect.py` through public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted apply/quote nesting around allocator restore input and the tree_hash before and after intern_tree validation path while controlling unknown or reserved operator bytes, so the code enabling or disabling an operator under the wrong dialect state, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that operator availability must follow active dialect and softfork state and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: wheel/python/clvm_rs/chia_dialect.py::Dialect
- Entrypoint: public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: unknown or reserved operator bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for apply/quote nesting around allocator restore, drive it through tree_hash before and after intern_tree, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

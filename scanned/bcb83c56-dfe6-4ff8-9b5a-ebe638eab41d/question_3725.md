# Q3725: opcode by name execution path result reused as next operator via tree_hash before and after intern_tree

## Question
Can an unprivileged attacker reach `opcode_by_name` in `src/f_table.rs` through public CLVM execution through `opcode_by_name` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted path result reused as next operator input and the tree_hash before and after intern_tree validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator availability must follow active dialect and softfork state and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/f_table.rs::opcode_by_name
- Entrypoint: public CLVM execution through `opcode_by_name` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for path result reused as next operator, drive it through tree_hash before and after intern_tree, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

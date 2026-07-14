# Q1833: softfork kw execution apply/quote nesting around allocator restore via node_to_bytes versus node_to_bytes_limit

## Question
Can an unprivileged attacker reach `softfork_kw` in `src/dialect.rs` through public CLVM execution through `softfork_kw` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted apply/quote nesting around allocator restore input and the node_to_bytes versus node_to_bytes_limit validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that same program/env/flags/max_cost must produce identical result, error, and cost and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/dialect.rs::softfork_kw
- Entrypoint: public CLVM execution through `softfork_kw` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for apply/quote nesting around allocator restore, drive it through node_to_bytes versus node_to_bytes_limit, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: same program/env/flags/max_cost must produce identical result, error, and cost
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

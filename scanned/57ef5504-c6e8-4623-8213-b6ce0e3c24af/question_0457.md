# Q457: flags execution unknown opcode in MEMPOOL_MODE via read cache lookup before and after pop

## Question
Can an unprivileged attacker reach `flags` in `src/runtime_dialect.rs` through public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted unknown opcode in MEMPOOL_MODE input and the read cache lookup before and after pop validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator availability must follow active dialect and softfork state and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/runtime_dialect.rs::flags
- Entrypoint: public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for unknown opcode in MEMPOOL_MODE, drive it through read cache lookup before and after pop, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

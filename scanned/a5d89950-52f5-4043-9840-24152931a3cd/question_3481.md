# Q3481: op execution softfork guard followed by disabled opcode via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `op` in `src/runtime_dialect.rs` through public CLVM execution through `op` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted softfork guard followed by disabled opcode input and the same bytes parsed under separate APIs validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator availability must follow active dialect and softfork state and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/runtime_dialect.rs::op
- Entrypoint: public CLVM execution through `op` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for softfork guard followed by disabled opcode, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

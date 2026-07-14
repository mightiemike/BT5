# Q1329: softfork kw execution cost one unit below required work via Python API versus Rust API

## Question
Can an unprivileged attacker reach `softfork_kw` in `src/dialect.rs` through public CLVM execution through `softfork_kw` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted cost one unit below required work input and the Python API versus Rust API validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that operator availability must follow active dialect and softfork state and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/dialect.rs::softfork_kw
- Entrypoint: public CLVM execution through `softfork_kw` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for cost one unit below required work, drive it through Python API versus Rust API, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

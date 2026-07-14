# Q2977: flags execution unknown opcode in MEMPOOL_MODE via round trip through tree hash and bytes

## Question
Can an unprivileged attacker reach `flags` in `src/runtime_dialect.rs` through public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted unknown opcode in MEMPOOL_MODE input and the round trip through tree hash and bytes validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cost and limit checks must precede consensus-visible output and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/runtime_dialect.rs::flags
- Entrypoint: public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for unknown opcode in MEMPOOL_MODE, drive it through round trip through tree hash and bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1023: run execution apply/quote nesting around allocator restore via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `run` in `src/run_program.rs` through public CLVM execution through `run` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted apply/quote nesting around allocator restore input and the serde_2026 direct versus serde auto validation path while controlling path-selected environment trees, so the code leaking softfork or allocator state into later evaluation, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that cost and limit checks must precede consensus-visible output and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: src/run_program.rs::run
- Entrypoint: public CLVM execution through `run` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: path-selected environment trees
- Exploit idea: Build the smallest CLVM blob/program/API call for apply/quote nesting around allocator restore, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

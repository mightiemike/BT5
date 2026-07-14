# Q835: softfork extension execution path result reused as next operator via strict canonical rejection versus successful round trip

## Question
Can an unprivileged attacker reach `softfork_extension` in `src/runtime_dialect.rs` through public CLVM execution through `softfork_extension` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted path result reused as next operator input and the strict canonical rejection versus successful round trip validation path while controlling quote/apply/softfork program atoms, so the code returning result/error/cost different from an equivalent supported path, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that operator availability must follow active dialect and softfork state and causing High dialect wiring error: disabled or mempool-forbidden operator becomes reachable?

## Target
- File/function: src/runtime_dialect.rs::softfork_extension
- Entrypoint: public CLVM execution through `softfork_extension` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: quote/apply/softfork program atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for path result reused as next operator, drive it through strict canonical rejection versus successful round trip, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: High dialect wiring error: disabled or mempool-forbidden operator becomes reachable
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

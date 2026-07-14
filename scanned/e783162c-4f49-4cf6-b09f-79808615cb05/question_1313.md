# Q1313: Dialect execution apply/quote nesting around allocator restore via default flags versus MEMPOOL_MODE

## Question
Can an unprivileged attacker reach `Dialect` in `wheel/python/clvm_rs/chia_dialect.py` through public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted apply/quote nesting around allocator restore input and the default flags versus MEMPOOL_MODE validation path while controlling quote/apply/softfork program atoms, so the code returning result/error/cost different from an equivalent supported path, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that operator availability must follow active dialect and softfork state and causing Critical consensus divergence: identical spend bytes evaluate differently?

## Target
- File/function: wheel/python/clvm_rs/chia_dialect.py::Dialect
- Entrypoint: public CLVM execution through `Dialect` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: quote/apply/softfork program atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for apply/quote nesting around allocator restore, drive it through default flags versus MEMPOOL_MODE, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: operator availability must follow active dialect and softfork state
- Expected Immunefi impact: Critical consensus divergence: identical spend bytes evaluate differently
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

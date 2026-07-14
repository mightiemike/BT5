# Q1087: flags execution unknown opcode in MEMPOOL_MODE via node_to_bytes versus node_to_bytes_limit

## Question
Can an unprivileged attacker reach `flags` in `src/runtime_dialect.rs` through public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper, using a crafted unknown opcode in MEMPOOL_MODE input and the node_to_bytes versus node_to_bytes_limit validation path while controlling quote/apply/softfork program atoms, so the code returning result/error/cost different from an equivalent supported path, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that cost and limit checks must precede consensus-visible output and causing High undercharged execution: cost/limit bypass affects mempool or consensus acceptance?

## Target
- File/function: src/runtime_dialect.rs::flags
- Entrypoint: public CLVM execution through `flags` using run_program, ChiaDialect, RuntimeDialect, or Python execution wrapper
- Attacker controls: quote/apply/softfork program atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for unknown opcode in MEMPOOL_MODE, drive it through node_to_bytes versus node_to_bytes_limit, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost and limit checks must precede consensus-visible output
- Expected Immunefi impact: High undercharged execution: cost/limit bypass affects mempool or consensus acceptance
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q67: op listp operator negative-zero-like atom via fresh allocator versus checkpoint restore

## Question
Can an unprivileged attacker reach `op_listp` in `src/core_ops.rs` through public CLVM execution through `op_listp` invoked by run_program or run_serialized_chia_program, using a crafted negative-zero-like atom input and the fresh allocator versus checkpoint restore validation path while controlling argument arity and improper-list shape, so the code returning result atom, pair, error, or cost different from CLVM semantics, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that cost must include all processed attacker bytes and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/core_ops.rs::op_listp
- Entrypoint: public CLVM execution through `op_listp` invoked by run_program or run_serialized_chia_program
- Attacker controls: argument arity and improper-list shape
- Exploit idea: Build the smallest CLVM blob/program/API call for negative-zero-like atom, drive it through fresh allocator versus checkpoint restore, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: cost must include all processed attacker bytes
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

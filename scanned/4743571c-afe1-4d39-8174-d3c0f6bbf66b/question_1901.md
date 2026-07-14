# Q1901: op lsh operator pair supplied where atom is required via pair path all-left versus all-right

## Question
Can an unprivileged attacker reach `op_lsh` in `src/more_ops.rs` through public CLVM execution through `op_lsh` invoked by run_program or run_serialized_chia_program, using a crafted pair supplied where atom is required input and the pair path all-left versus all-right validation path while controlling argument arity and improper-list shape, so the code returning result atom, pair, error, or cost different from CLVM semantics, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that fast paths must equal generic bignum behavior and causing High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec?

## Target
- File/function: src/more_ops.rs::op_lsh
- Entrypoint: public CLVM execution through `op_lsh` invoked by run_program or run_serialized_chia_program
- Attacker controls: argument arity and improper-list shape
- Exploit idea: Build the smallest CLVM blob/program/API call for pair supplied where atom is required, drive it through pair path all-left versus all-right, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: fast paths must equal generic bignum behavior
- Expected Immunefi impact: High numeric semantic mismatch: arithmetic or atom behavior violates CLVM spec
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3529: new g2 core empty atom versus nil boundary via Program.run_with_cost versus run_serialized_chia_program

## Question
Can an unprivileged attacker reach `new_g2` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_g2`, using a crafted empty atom versus nil boundary input and the Program.run_with_cost versus run_serialized_chia_program validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/allocator.rs::new_g2
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_g2`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through Program.run_with_cost versus run_serialized_chia_program, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

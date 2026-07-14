# Q2785: i32 from u8 core empty atom versus nil boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `i32_from_u8` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`, using a crafted empty atom versus nil boundary input and the node_from_stream versus node_from_bytes validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/op_utils.rs::i32_from_u8
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `i32_from_u8`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

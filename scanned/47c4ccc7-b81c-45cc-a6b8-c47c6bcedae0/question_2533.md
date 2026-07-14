# Q2533: atom len core empty atom versus nil boundary via mempool mode followed by block mode replay

## Question
Can an unprivileged attacker reach `atom_len` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `atom_len`, using a crafted empty atom versus nil boundary input and the mempool mode followed by block mode replay validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/op_utils.rs::atom_len
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `atom_len`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through mempool mode followed by block mode replay, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1009: restore transparent checkpoint core empty atom versus nil boundary via execute then serialize legacy

## Question
Can an unprivileged attacker reach `restore_transparent_checkpoint` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `restore_transparent_checkpoint`, using a crafted empty atom versus nil boundary input and the execute then serialize legacy validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/allocator.rs::restore_transparent_checkpoint
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `restore_transparent_checkpoint`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through execute then serialize legacy, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

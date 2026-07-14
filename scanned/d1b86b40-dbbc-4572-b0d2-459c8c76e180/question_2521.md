# Q2521: new limited core empty atom versus nil boundary via Python Program wrapper versus low-level LazyNode

## Question
Can an unprivileged attacker reach `new_limited` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_limited`, using a crafted empty atom versus nil boundary input and the Python Program wrapper versus low-level LazyNode validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/allocator.rs::new_limited
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_limited`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through Python Program wrapper versus low-level LazyNode, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1513: new malachite number core empty atom versus nil boundary via allocator debug semantics versus release semantics

## Question
Can an unprivileged attacker reach `new_malachite_number` in `src/allocator.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `new_malachite_number`, using a crafted empty atom versus nil boundary input and the allocator debug semantics versus release semantics validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/allocator.rs::new_malachite_number
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `new_malachite_number`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through allocator debug semantics versus release semantics, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

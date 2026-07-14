# Q3227: reduction core allocator restore after pair creation via parse then execute

## Question
Can an unprivileged attacker reach `reduction` in `src/reduction.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `reduction`, using a crafted allocator restore after pair creation input and the parse then execute validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that path traversal must match CLVM first/rest semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/reduction.rs::reduction
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `reduction`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for allocator restore after pair creation, drive it through parse then execute, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

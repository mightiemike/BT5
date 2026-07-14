# Q1405: hash blob core empty atom versus nil boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `hash_blob` in `src/serde/bytes32.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `hash_blob`, using a crafted empty atom versus nil boundary input and the node_from_stream versus node_from_bytes validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that integer helpers must agree with operator semantics and causing Critical consensus divergence: core helpers make equivalent paths disagree?

## Target
- File/function: src/serde/bytes32.rs::hash_blob
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `hash_blob`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: integer helpers must agree with operator semantics
- Expected Immunefi impact: Critical consensus divergence: core helpers make equivalent paths disagree
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

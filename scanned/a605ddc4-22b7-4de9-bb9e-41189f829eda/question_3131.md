# Q3131: traverse path fast core allocator restore after pair creation via writer limit at exact output length

## Question
Can an unprivileged attacker reach `traverse_path_fast` in `src/traverse_path.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `traverse_path_fast`, using a crafted allocator restore after pair creation input and the writer limit at exact output length validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that valid NodePtr type/identity must remain stable and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/traverse_path.rs::traverse_path_fast
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `traverse_path_fast`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for allocator restore after pair creation, drive it through writer limit at exact output length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

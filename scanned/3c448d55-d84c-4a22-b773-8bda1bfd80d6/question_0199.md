# Q199: lib core empty atom versus nil boundary via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `lib` in `src/lib.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `lib`, using a crafted empty atom versus nil boundary input and the same bytes parsed under separate APIs validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that path traversal must match CLVM first/rest semantics and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/lib.rs::lib
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `lib`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: path traversal must match CLVM first/rest semantics
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

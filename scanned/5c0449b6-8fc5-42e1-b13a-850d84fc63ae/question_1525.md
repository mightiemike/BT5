# Q1525: rest core empty atom versus nil boundary via strict canonical rejection versus successful round trip

## Question
Can an unprivileged attacker reach `rest` in `src/op_utils.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `rest`, using a crafted empty atom versus nil boundary input and the strict canonical rejection versus successful round trip validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that tree hash must use exact atom bytes and pair order and causing Critical tree identity corruption: atom/pair/path/hash changes?

## Target
- File/function: src/op_utils.rs::rest
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `rest`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for empty atom versus nil boundary, drive it through strict canonical rejection versus successful round trip, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: tree hash must use exact atom bytes and pair order
- Expected Immunefi impact: Critical tree identity corruption: atom/pair/path/hash changes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

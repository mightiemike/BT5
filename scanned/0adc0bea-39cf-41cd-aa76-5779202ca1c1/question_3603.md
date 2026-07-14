# Q3603: bits core path leading zero bytes via stream hash versus tree hash

## Question
Can an unprivileged attacker reach `bits` in `src/number.rs` through public CLVM parse, execute, traverse, hash, or allocator API through `bits`, using a crafted path leading zero bytes input and the stream hash versus tree hash validation path while controlling small-atom and heap-atom boundary bytes, so the code changing atom/pair identity across equivalent APIs, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that valid NodePtr type/identity must remain stable and causing High numeric semantic mismatch: helper parsing differs from CLVM semantics?

## Target
- File/function: src/number.rs::bits
- Entrypoint: public CLVM parse, execute, traverse, hash, or allocator API through `bits`
- Attacker controls: small-atom and heap-atom boundary bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for path leading zero bytes, drive it through stream hash versus tree hash, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid NodePtr type/identity must remain stable
- Expected Immunefi impact: High numeric semantic mismatch: helper parsing differs from CLVM semantics
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

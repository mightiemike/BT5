# Q3487: sha blobs parser trailing bytes after valid tree via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `sha_blobs` in `src/serde/de_tree.rs` through public parsing or stream-analysis through `sha_blobs` before execution, hashing, or serialization, using a crafted trailing bytes after valid tree input and the nil atom reused inside pair validation path while controlling canonical and non-canonical atom length prefixes, so the code accepting bytes another canonical parser rejects, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that bytes consumed, serialized length, and cursor position must agree and causing Critical tree identity corruption: parsed tree/hash differs from encoded bytes?

## Target
- File/function: src/serde/de_tree.rs::sha_blobs
- Entrypoint: public parsing or stream-analysis through `sha_blobs` before execution, hashing, or serialization
- Attacker controls: canonical and non-canonical atom length prefixes
- Exploit idea: Build the smallest CLVM blob/program/API call for trailing bytes after valid tree, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: bytes consumed, serialized length, and cursor position must agree
- Expected Immunefi impact: Critical tree identity corruption: parsed tree/hash differs from encoded bytes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

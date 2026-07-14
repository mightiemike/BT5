# Q587: node from stream parser deep cons marker nesting via fast path versus generic path

## Question
Can an unprivileged attacker reach `node_from_stream` in `src/serde/de.rs` through public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization, using a crafted deep cons marker nesting input and the fast path versus generic path validation path while controlling canonical and non-canonical atom length prefixes, so the code accepting bytes another canonical parser rejects, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that ambiguous or non-canonical serialization must reject and causing Critical tree identity corruption: parsed tree/hash differs from encoded bytes?

## Target
- File/function: src/serde/de.rs::node_from_stream
- Entrypoint: public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization
- Attacker controls: canonical and non-canonical atom length prefixes
- Exploit idea: Build the smallest CLVM blob/program/API call for deep cons marker nesting, drive it through fast path versus generic path, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: ambiguous or non-canonical serialization must reject
- Expected Immunefi impact: Critical tree identity corruption: parsed tree/hash differs from encoded bytes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

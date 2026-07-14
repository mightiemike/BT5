# Q1921: node to stream serializer single-byte atom serialization boundary via pre-eval callback enabled versus disabled

## Question
Can an unprivileged attacker reach `node_to_stream` in `src/serde/ser.rs` through public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted single-byte atom serialization boundary input and the pre-eval callback enabled versus disabled validation path while controlling valid trees with boundary-size atoms, so the code emitting non-canonical bytes for a valid tree, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that writer limits must not produce accepted partial encodings and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/ser.rs::node_to_stream
- Entrypoint: public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: valid trees with boundary-size atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for single-byte atom serialization boundary, drive it through pre-eval callback enabled versus disabled, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: writer limits must not produce accepted partial encodings
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

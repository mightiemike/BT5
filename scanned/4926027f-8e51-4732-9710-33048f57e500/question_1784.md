# Q1784: node from bytes parser truncated atom length prefix via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `node_from_bytes` in `src/serde/de.rs` through public parsing or stream-analysis through `node_from_bytes` before execution, hashing, or serialization, using a crafted truncated atom length prefix input and the same bytes parsed under separate APIs validation path while controlling deep cons-box structures and single-byte atom boundaries, so the code confusing atom length, cursor position, or pair construction, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that stream tree hash must equal deserialize-then-hash and causing Critical consensus divergence: one supported path accepts bytes another rejects?

## Target
- File/function: src/serde/de.rs::node_from_bytes
- Entrypoint: public parsing or stream-analysis through `node_from_bytes` before execution, hashing, or serialization
- Attacker controls: deep cons-box structures and single-byte atom boundaries
- Exploit idea: Build the smallest CLVM blob/program/API call for truncated atom length prefix, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: stream tree hash must equal deserialize-then-hash
- Expected Immunefi impact: Critical consensus divergence: one supported path accepts bytes another rejects
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

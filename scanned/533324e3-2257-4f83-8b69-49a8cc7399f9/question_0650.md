# Q650: node from bytes parser truncated atom length prefix via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `node_from_bytes` in `src/serde/de.rs` through public parsing or stream-analysis through `node_from_bytes` before execution, hashing, or serialization, using a crafted truncated atom length prefix input and the serde_2026 direct versus serde auto validation path while controlling truncated streams and trailing bytes, so the code returning tree/hash/length inconsistent with bytes consumed, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that canonical bytes must map to one exact tree and causing Critical consensus divergence: one supported path accepts bytes another rejects?

## Target
- File/function: src/serde/de.rs::node_from_bytes
- Entrypoint: public parsing or stream-analysis through `node_from_bytes` before execution, hashing, or serialization
- Attacker controls: truncated streams and trailing bytes
- Exploit idea: Build the smallest CLVM blob/program/API call for truncated atom length prefix, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: canonical bytes must map to one exact tree
- Expected Immunefi impact: Critical consensus divergence: one supported path accepts bytes another rejects
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

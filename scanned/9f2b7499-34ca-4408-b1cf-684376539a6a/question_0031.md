# Q31: node to stream serializer single-byte atom serialization boundary via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `node_to_stream` in `src/serde/ser.rs` through public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted single-byte atom serialization boundary input and the same bytes parsed under separate APIs validation path while controlling writer limit values exposed by API callers, so the code failing a limit check after producing accepted partial encoding, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that serialization must round-trip to same tree and hash and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/ser.rs::node_to_stream
- Entrypoint: public serialization through `node_to_stream` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: writer limit values exposed by API callers
- Exploit idea: Build the smallest CLVM blob/program/API call for single-byte atom serialization boundary, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: serialization must round-trip to same tree and hash
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

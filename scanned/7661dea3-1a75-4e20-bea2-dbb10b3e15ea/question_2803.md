# Q2803: node to bytes serializer single-byte atom serialization boundary via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `node_to_bytes` in `src/serde/ser.rs` through public serialization through `node_to_bytes` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted single-byte atom serialization boundary input and the direct parse versus auto-detect parse validation path while controlling writer limit values exposed by API callers, so the code failing a limit check after producing accepted partial encoding, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that serialization must round-trip to same tree and hash and causing High Python/Rust API divergence: callers see different bytes for same tree?

## Target
- File/function: src/serde/ser.rs::node_to_bytes
- Entrypoint: public serialization through `node_to_bytes` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: writer limit values exposed by API callers
- Exploit idea: Build the smallest CLVM blob/program/API call for single-byte atom serialization boundary, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: serialization must round-trip to same tree and hash
- Expected Immunefi impact: High Python/Rust API divergence: callers see different bytes for same tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3307: node to bytes limit serializer single-byte atom serialization boundary via node_from_stream versus node_from_bytes

## Question
Can an unprivileged attacker reach `node_to_bytes_limit` in `src/serde/ser.rs` through public serialization through `node_to_bytes_limit` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted single-byte atom serialization boundary input and the node_from_stream versus node_from_bytes validation path while controlling writer limit values exposed by API callers, so the code failing a limit check after producing accepted partial encoding, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that serialization must round-trip to same tree and hash and causing Critical tree identity corruption: serialization changes tree/hash?

## Target
- File/function: src/serde/ser.rs::node_to_bytes_limit
- Entrypoint: public serialization through `node_to_bytes_limit` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: writer limit values exposed by API callers
- Exploit idea: Build the smallest CLVM blob/program/API call for single-byte atom serialization boundary, drive it through node_from_stream versus node_from_bytes, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: serialization must round-trip to same tree and hash
- Expected Immunefi impact: Critical tree identity corruption: serialization changes tree/hash
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3233: node from stream parser deep cons marker nesting via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `node_from_stream` in `src/serde/de.rs` through public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization, using a crafted deep cons marker nesting input and the strict mode versus non-strict mode where exposed validation path while controlling backreference bytes where accepted by the path, so the code reporting success for bytes that deserialize differently later, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that bytes consumed, serialized length, and cursor position must agree and causing Critical tree identity corruption: parsed tree/hash differs from encoded bytes?

## Target
- File/function: src/serde/de.rs::node_from_stream
- Entrypoint: public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization
- Attacker controls: backreference bytes where accepted by the path
- Exploit idea: Build the smallest CLVM blob/program/API call for deep cons marker nesting, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: bytes consumed, serialized length, and cursor position must agree
- Expected Immunefi impact: Critical tree identity corruption: parsed tree/hash differs from encoded bytes
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q3485: node from stream parser deep cons marker nesting via maximum small atom then heap atom

## Question
Can an unprivileged attacker reach `node_from_stream` in `src/serde/de.rs` through public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization, using a crafted deep cons marker nesting input and the maximum small atom then heap atom validation path while controlling backreference bytes where accepted by the path, so the code reporting success for bytes that deserialize differently later, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that bytes consumed, serialized length, and cursor position must agree and causing Critical consensus divergence: one supported path accepts bytes another rejects?

## Target
- File/function: src/serde/de.rs::node_from_stream
- Entrypoint: public parsing or stream-analysis through `node_from_stream` before execution, hashing, or serialization
- Attacker controls: backreference bytes where accepted by the path
- Exploit idea: Build the smallest CLVM blob/program/API call for deep cons marker nesting, drive it through maximum small atom then heap atom, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: bytes consumed, serialized length, and cursor position must agree
- Expected Immunefi impact: Critical consensus divergence: one supported path accepts bytes another rejects
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

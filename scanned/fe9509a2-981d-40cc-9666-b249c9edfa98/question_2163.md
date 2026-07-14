# Q2163: node from stream backrefs parser 0x7f versus 0x80 atom boundary via nil atom reused inside pair

## Question
Can an unprivileged attacker reach `node_from_stream_backrefs` in `src/serde/de_br.rs` through public parsing or stream-analysis through `node_from_stream_backrefs` before execution, hashing, or serialization, using a crafted 0x7f versus 0x80 atom boundary input and the nil atom reused inside pair validation path while controlling backreference bytes where accepted by the path, so the code reporting success for bytes that deserialize differently later, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that stream tree hash must equal deserialize-then-hash and causing Critical canonical serialization failure: ambiguous bytes become accepted?

## Target
- File/function: src/serde/de_br.rs::node_from_stream_backrefs
- Entrypoint: public parsing or stream-analysis through `node_from_stream_backrefs` before execution, hashing, or serialization
- Attacker controls: backreference bytes where accepted by the path
- Exploit idea: Build the smallest CLVM blob/program/API call for 0x7f versus 0x80 atom boundary, drive it through nil atom reused inside pair, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: stream tree hash must equal deserialize-then-hash
- Expected Immunefi impact: Critical canonical serialization failure: ambiguous bytes become accepted
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

# Q1281: node from bytes backrefs parser 0x7f versus 0x80 atom boundary via deserialize then serialized_length

## Question
Can an unprivileged attacker reach `node_from_bytes_backrefs` in `src/serde/de_br.rs` through public parsing or stream-analysis through `node_from_bytes_backrefs` before execution, hashing, or serialization, using a crafted 0x7f versus 0x80 atom boundary input and the deserialize then serialized_length validation path while controlling canonical and non-canonical atom length prefixes, so the code accepting bytes another canonical parser rejects, given that no privileged role, leaked key, admin action, trusted operator, or mainnet testing is required, violating the invariant that canonical bytes must map to one exact tree and causing Critical tree identity corruption: parsed tree/hash differs from encoded bytes?

## Target
- File/function: src/serde/de_br.rs::node_from_bytes_backrefs
- Entrypoint: public parsing or stream-analysis through `node_from_bytes_backrefs` before execution, hashing, or serialization
- Attacker controls: canonical and non-canonical atom length prefixes
- Exploit idea: Build the smallest CLVM blob/program/API call for 0x7f versus 0x80 atom boundary, drive it through deserialize then serialized_length, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: canonical bytes must map to one exact tree
- Expected Immunefi impact: Critical tree identity corruption: parsed tree/hash differs from encoded bytes
- Fast validation: write a Rust regression test and Python wheel comparison for exact result/error/cost/bytes/hash agreement; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

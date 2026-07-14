# Q2683: write atom serializer single-byte atom serialization boundary via maximum small atom then heap atom

## Question
Can an unprivileged attacker reach `write_atom` in `src/serde/write_atom.rs` through public serialization through `write_atom` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted single-byte atom serialization boundary input and the maximum small atom then heap atom validation path while controlling writer limit values exposed by API callers, so the code failing a limit check after producing accepted partial encoding, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that writer limits must not produce accepted partial encodings and causing Critical canonical serialization failure: emitted bytes decode ambiguously?

## Target
- File/function: src/serde/write_atom.rs::write_atom
- Entrypoint: public serialization through `write_atom` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: writer limit values exposed by API callers
- Exploit idea: Build the smallest CLVM blob/program/API call for single-byte atom serialization boundary, drive it through maximum small atom then heap atom, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: writer limits must not produce accepted partial encodings
- Expected Immunefi impact: Critical canonical serialization failure: emitted bytes decode ambiguously
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

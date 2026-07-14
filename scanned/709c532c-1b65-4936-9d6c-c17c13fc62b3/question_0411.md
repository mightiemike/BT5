# Q411: atom length bits serializer writer limit exactly after prefix via parse then execute

## Question
Can an unprivileged attacker reach `atom_length_bits` in `src/serde/serialized_length.rs` through public serialization through `atom_length_bits` after attacker-controlled CLVM bytes are parsed into a tree, using a crafted writer limit exactly after prefix input and the parse then execute validation path while controlling writer limit values exposed by API callers, so the code failing a limit check after producing accepted partial encoding, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that writer limits must not produce accepted partial encodings and causing High Python/Rust API divergence: callers see different bytes for same tree?

## Target
- File/function: src/serde/serialized_length.rs::atom_length_bits
- Entrypoint: public serialization through `atom_length_bits` after attacker-controlled CLVM bytes are parsed into a tree
- Attacker controls: writer limit values exposed by API callers
- Exploit idea: Build the smallest CLVM blob/program/API call for writer limit exactly after prefix, drive it through parse then execute, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: writer limits must not produce accepted partial encodings
- Expected Immunefi impact: High Python/Rust API divergence: callers see different bytes for same tree
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

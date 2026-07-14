# Q799: op sha256 tree crypto BLS infinity encoding via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `op_sha256_tree` in `src/sha_tree_op.rs` through public CLVM execution through `op_sha256_tree` invoked by a spend using crypto/hash opcodes, using a crafted BLS infinity encoding input and the serde_2026 direct versus serde auto validation path while controlling empty and boundary-length message atoms, so the code handling relaxed mode or subgroup checks inconsistently, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that invalid signatures, points, proofs, and hashes must never validate and causing Critical crypto/hash semantic failure: invalid proof material validates?

## Target
- File/function: src/sha_tree_op.rs::op_sha256_tree
- Entrypoint: public CLVM execution through `op_sha256_tree` invoked by a spend using crypto/hash opcodes
- Attacker controls: empty and boundary-length message atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS infinity encoding, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: invalid signatures, points, proofs, and hashes must never validate
- Expected Immunefi impact: Critical crypto/hash semantic failure: invalid proof material validates
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

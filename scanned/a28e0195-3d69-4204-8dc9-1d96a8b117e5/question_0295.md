# Q295: op sha256 tree crypto BLS infinity encoding via mempool mode followed by block mode replay

## Question
Can an unprivileged attacker reach `op_sha256_tree` in `src/sha_tree_op.rs` through public CLVM execution through `op_sha256_tree` invoked by a spend using crypto/hash opcodes, using a crafted BLS infinity encoding input and the mempool mode followed by block mode replay validation path while controlling empty and boundary-length message atoms, so the code handling relaxed mode or subgroup checks inconsistently, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that invalid signatures, points, proofs, and hashes must never validate and causing Critical consensus divergence: valid crypto material is accepted by one path and rejected by another?

## Target
- File/function: src/sha_tree_op.rs::op_sha256_tree
- Entrypoint: public CLVM execution through `op_sha256_tree` invoked by a spend using crypto/hash opcodes
- Attacker controls: empty and boundary-length message atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS infinity encoding, drive it through mempool mode followed by block mode replay, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: invalid signatures, points, proofs, and hashes must never validate
- Expected Immunefi impact: Critical consensus divergence: valid crypto material is accepted by one path and rejected by another
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

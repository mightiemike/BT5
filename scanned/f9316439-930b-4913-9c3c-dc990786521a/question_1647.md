# Q1647: op keccak256 crypto secp valid-length invalid signature via direct parse versus auto-detect parse

## Question
Can an unprivileged attacker reach `op_keccak256` in `src/keccak256_ops.rs` through public CLVM execution through `op_keccak256` invoked by a spend using crypto/hash opcodes, using a crafted secp valid-length invalid signature input and the direct parse versus auto-detect parse validation path while controlling empty and boundary-length message atoms, so the code handling relaxed mode or subgroup checks inconsistently, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that crypto/hash output must be deterministic for exact atom bytes and causing Critical consensus divergence: valid crypto material is accepted by one path and rejected by another?

## Target
- File/function: src/keccak256_ops.rs::op_keccak256
- Entrypoint: public CLVM execution through `op_keccak256` invoked by a spend using crypto/hash opcodes
- Attacker controls: empty and boundary-length message atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for secp valid-length invalid signature, drive it through direct parse versus auto-detect parse, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: crypto/hash output must be deterministic for exact atom bytes
- Expected Immunefi impact: Critical consensus divergence: valid crypto material is accepted by one path and rejected by another
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

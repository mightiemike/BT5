# Q3923: op secp256r1 verify crypto oversized message cost boundary via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `op_secp256r1_verify` in `src/secp_ops.rs` through public CLVM execution through `op_secp256r1_verify` invoked by a spend using crypto/hash opcodes, using a crafted oversized message cost boundary input and the strict mode versus non-strict mode where exposed validation path while controlling empty and boundary-length message atoms, so the code handling relaxed mode or subgroup checks inconsistently, given that the path is reachable through documented clvm_rs Rust or Python APIs used by wallets, nodes, or testnet services, violating the invariant that crypto/hash output must be deterministic for exact atom bytes and causing Critical crypto/hash semantic failure: invalid proof material validates?

## Target
- File/function: src/secp_ops.rs::op_secp256r1_verify
- Entrypoint: public CLVM execution through `op_secp256r1_verify` invoked by a spend using crypto/hash opcodes
- Attacker controls: empty and boundary-length message atoms
- Exploit idea: Build the smallest CLVM blob/program/API call for oversized message cost boundary, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: crypto/hash output must be deterministic for exact atom bytes
- Expected Immunefi impact: Critical crypto/hash semantic failure: invalid proof material validates
- Fast validation: compare direct API, round-trip API, and reference CLVM behavior on the same crafted input; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

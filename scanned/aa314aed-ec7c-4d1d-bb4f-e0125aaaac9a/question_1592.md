# Q1592: op secp256k1 verify crypto BLS subgroup boundary via strict mode versus non-strict mode where exposed

## Question
Can an unprivileged attacker reach `op_secp256k1_verify` in `src/secp_ops.rs` through public CLVM execution through `op_secp256k1_verify` invoked by a spend using crypto/hash opcodes, using a crafted BLS subgroup boundary input and the strict mode versus non-strict mode where exposed validation path while controlling valid-length invalid-subgroup or infinity encodings, so the code hashing/verifying bytes different from the exact atom, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that valid Chia-compatible inputs must not diverge across APIs and causing High undercharged crypto execution: expensive verification or hashing is undercharged?

## Target
- File/function: src/secp_ops.rs::op_secp256k1_verify
- Entrypoint: public CLVM execution through `op_secp256k1_verify` invoked by a spend using crypto/hash opcodes
- Attacker controls: valid-length invalid-subgroup or infinity encodings
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS subgroup boundary, drive it through strict mode versus non-strict mode where exposed, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: valid Chia-compatible inputs must not diverge across APIs
- Expected Immunefi impact: High undercharged crypto execution: expensive verification or hashing is undercharged
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

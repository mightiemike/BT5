# Q1262: op bls verify crypto BLS subgroup boundary via same bytes parsed under separate APIs

## Question
Can an unprivileged attacker reach `op_bls_verify` in `src/bls_ops.rs` through public CLVM execution through `op_bls_verify` invoked by a spend using crypto/hash opcodes, using a crafted BLS subgroup boundary input and the same bytes parsed under separate APIs validation path while controlling valid-length invalid-subgroup or infinity encodings, so the code hashing/verifying bytes different from the exact atom, given that the attacker supplies only CLVM bytes, puzzle/solution data, exposed flags, or Python API inputs, violating the invariant that crypto/hash output must be deterministic for exact atom bytes and causing Critical crypto/hash semantic failure: invalid proof material validates?

## Target
- File/function: src/bls_ops.rs::op_bls_verify
- Entrypoint: public CLVM execution through `op_bls_verify` invoked by a spend using crypto/hash opcodes
- Attacker controls: valid-length invalid-subgroup or infinity encodings
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS subgroup boundary, drive it through same bytes parsed under separate APIs, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: crypto/hash output must be deterministic for exact atom bytes
- Expected Immunefi impact: Critical crypto/hash semantic failure: invalid proof material validates
- Fast validation: add a property/fuzz seed and reject unless consensus-visible result, canonical bytes, cost, or tree hash changes; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

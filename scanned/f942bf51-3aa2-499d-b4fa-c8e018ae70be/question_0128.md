# Q128: op bls g2 add crypto BLS subgroup boundary via serde_2026 direct versus serde auto

## Question
Can an unprivileged attacker reach `op_bls_g2_add` in `src/bls_ops.rs` through public CLVM execution through `op_bls_g2_add` invoked by a spend using crypto/hash opcodes, using a crafted BLS subgroup boundary input and the serde_2026 direct versus serde auto validation path while controlling mixed valid and invalid crypto arguments, so the code charging fewer bytes or pairings than verified, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that invalid signatures, points, proofs, and hashes must never validate and causing Critical crypto/hash semantic failure: invalid proof material validates?

## Target
- File/function: src/bls_ops.rs::op_bls_g2_add
- Entrypoint: public CLVM execution through `op_bls_g2_add` invoked by a spend using crypto/hash opcodes
- Attacker controls: mixed valid and invalid crypto arguments
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS subgroup boundary, drive it through serde_2026 direct versus serde auto, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: invalid signatures, points, proofs, and hashes must never validate
- Expected Immunefi impact: Critical crypto/hash semantic failure: invalid proof material validates
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

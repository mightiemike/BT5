# Q1466: op secp256k1 verify crypto BLS subgroup boundary via same tree allocated twice in distinct allocators

## Question
Can an unprivileged attacker reach `op_secp256k1_verify` in `src/secp_ops.rs` through public CLVM execution through `op_secp256k1_verify` invoked by a spend using crypto/hash opcodes, using a crafted BLS subgroup boundary input and the same tree allocated twice in distinct allocators validation path while controlling mixed valid and invalid crypto arguments, so the code charging fewer bytes or pairings than verified, given that the hypothesis is rejected if the only effect is crash, DoS, slowdown, docs/tests, scripts, disabled config, or downstream misuse, violating the invariant that crypto cost must match actual inputs and causing Critical crypto/hash semantic failure: invalid proof material validates?

## Target
- File/function: src/secp_ops.rs::op_secp256k1_verify
- Entrypoint: public CLVM execution through `op_secp256k1_verify` invoked by a spend using crypto/hash opcodes
- Attacker controls: mixed valid and invalid crypto arguments
- Exploit idea: Build the smallest CLVM blob/program/API call for BLS subgroup boundary, drive it through same tree allocated twice in distinct allocators, and compare result node, error class, cost, serialized bytes, and tree hash against the equivalent supported path.
- Invariant to test: crypto cost must match actual inputs
- Expected Immunefi impact: Critical crypto/hash semantic failure: invalid proof material validates
- Fast validation: construct two distinct inputs and assert no parser, serializer, cache, or binding path merges them; reject out-of-scope crash/DoS/performance-only/docs/tests/scripts/disabled-config/downstream-misuse outcomes.

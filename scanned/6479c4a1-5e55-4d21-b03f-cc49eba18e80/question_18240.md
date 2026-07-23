# Q18240: contract-visible crypto inconsistency in vrf::public_key

## Question
Can an unprivileged attacker call a contract method that depends on cryptographic host outputs that reaches `core/crypto/src/vrf.rs::public_key` with control over bounded inputs that should produce one deterministic cryptographic result and make nearcore return inconsistent verification or hash outputs for the same logical input across execution contexts, breaking the invariant that cryptographic host outputs must be deterministic and context-stable for the same input, and leading to contracts execution flows?

## Target
- File/function: `core/crypto/src/vrf.rs::public_key`
- Entrypoint: call a contract method that depends on cryptographic host outputs
- Attacker controls: bounded inputs that should produce one deterministic cryptographic result
- Exploit idea: return inconsistent verification or hash outputs for the same logical input across execution contexts
- Invariant to test: cryptographic host outputs must be deterministic and context-stable for the same input
- Expected Immunefi impact: Contracts execution flows
- Fast validation: write a repeated-execution test for the same crypto host input and assert identical outputs and charges

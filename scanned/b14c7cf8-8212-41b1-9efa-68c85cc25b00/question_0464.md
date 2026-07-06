# Q464: Signature binding gap around productId

## Question
Can an unprivileged user reach core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) through a normal Nado flow where the executed state change depends on productId, but the accepted signature or digest path fails to bind productId tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User submits signed endpoint payloads that EndpointTx verifies through Verifier.computeDigest(...), validateSignature(...), or validateCompactSignature(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Mutate productId after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask).
- Invariant to test: Every accepted signature must bind the exact action, sender, market context, amount, recipient, and nonce that the protocol executes.
- Expected HackenProof impact: Critical/High: unauthorized transaction through signature bypass or digest mismatch.
- Fast validation: Fuzz every signed field and assert that any semantic mutation changes the digest and invalidates both full and compact signatures.

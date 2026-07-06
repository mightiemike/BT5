# Q571: Type confusion between signed intent and executed path

## Question
Can an attacker craft calldata or a signed payload so that core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) validates one semantic action but decodes or executes another semantic action with a different effect on balances, positions, recipients, or signers?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User submits signed endpoint payloads that EndpointTx verifies through Verifier.computeDigest(...), validateSignature(...), or validateCompactSignature(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Cross-check the validated digest fields against the later decode/dispatch logic in core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask), especially where transaction type, appendix bits, recipient, or derived subaccount state influence execution.
- Invariant to test: Every accepted signature must bind the exact action, sender, market context, amount, recipient, and nonce that the protocol executes.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation via action-type confusion.
- Fast validation: Fuzz every signed field and assert that any semantic mutation changes the digest and invalidates both full and compact signatures.

# Q227: Nonce consume mismatch across fail, cancel, or alternate path

## Question
Can the same nonce, idx, or fill marker around core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) be left unused on one path but considered consumed on another, allowing replay on the favorable branch or grief-free reuse after partial execution?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User or relayer submits fast-withdrawal signatures that WithdrawPool verifies through Verifier.requireValidTxSignatures(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Exercise success, revert, partial-fill, cancel, and alternate-recipient branches around core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask); then compare whether replay protection is consumed consistently across all economically equivalent paths.
- Invariant to test: Replay protection must be consumed exactly once for each signed or queued instruction, regardless of which reachable execution branch is taken.
- Expected HackenProof impact: Critical/High: unauthorized transaction, replay, or transaction manipulation through inconsistent nonce consumption.
- Fast validation: Fuzz every signed field and assert that any semantic mutation changes the digest and invalidates both full and compact signatures.

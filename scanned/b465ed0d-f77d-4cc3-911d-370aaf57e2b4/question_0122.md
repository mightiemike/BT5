# Q122: Cross-contract desync of pubkeys

## Question
Can a normal user drive core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) so that pubkeys is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User submits signed endpoint payloads that EndpointTx verifies through Verifier.computeDigest(...), validateSignature(...), or validateCompactSignature(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Target the exact moment when core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) mutates pubkeys and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Linked-signer and compact-signature handling must not expand the authority of an unprivileged user beyond the intended sender context.
- Expected HackenProof impact: Critical/High: stealing or loss of funds by reusing a valid signature for a different settlement path.
- Fast validation: Cross-check Verifier.computeDigest(...) against independently re-encoded test vectors for every signed transaction type in scope.

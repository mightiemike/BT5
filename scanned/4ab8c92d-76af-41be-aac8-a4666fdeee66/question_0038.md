# Q38: Chain, domain, or contract binding gap

## Question
Can authorization accepted by core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) be replayed across a different chain, proxy implementation, verifying contract, or helper context because the signed domain does not fully match the execution domain?

## Target
- File/function: core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask)
- Entrypoint: User submits signed endpoint payloads that EndpointTx verifies through Verifier.computeDigest(...), validateSignature(...), or validateCompactSignature(...).
- Attacker controls: transaction type, transaction body, sender, recipient, productId, amount, nonce, sendTo, appendix, idx
- Exploit idea: Recreate the same signed payload under alternate chainId, proxy, helper, verifying-contract, or domain-separator contexts and check whether core/contracts/Verifier.sol / requireValidSignature(bytes32 message, bytes32 e, bytes32 s, uint8 signerBitmask) still accepts it for a different live execution surface.
- Invariant to test: Signed actions must bind the exact live Nado execution domain and must not survive a change in chain, contract, proxy, or helper context.
- Expected HackenProof impact: Critical/High: replay or unauthorized transaction through insufficient domain separation.
- Fast validation: Build a withdrawal replay test that varies chain ID, idx, sendTo, and transaction bytes around requireValidTxSignatures(...).

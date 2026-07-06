# Q2152: Signature binding gap around sendTo

## Question
Can an unprivileged user reach core/contracts/EndpointTx.sol / validateCompactSignature(bytes32 sender, bytes32 digest, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner) through a normal Nado flow where the executed state change depends on sendTo, but the accepted signature or digest path fails to bind sendTo tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/EndpointTx.sol / validateCompactSignature(bytes32 sender, bytes32 digest, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Mutate sendTo after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/EndpointTx.sol / validateCompactSignature(bytes32 sender, bytes32 digest, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner).
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or unauthorized account/subaccount mutation.
- Fast validation: Write a Hardhat test that reuses the same signed payload while mutating one semantic field at a time and assert EndpointTx rejects every mutation.

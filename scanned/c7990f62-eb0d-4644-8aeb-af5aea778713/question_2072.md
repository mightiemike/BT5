# Q2072: Replay or cross-context reuse of productId

## Question
Can a signature or signed payload accepted by core/contracts/EndpointTx.sol / validateCompactSignature(bytes32 sender, bytes32 digest, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner) be replayed in a different context where productId changes meaning, allowing the attacker to reuse valid authorization for a different economic effect?

## Target
- File/function: core/contracts/EndpointTx.sol / validateCompactSignature(bytes32 sender, bytes32 digest, IEndpoint.CompactSignature memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Try to replay the same signed bytes after mutating only the execution context for productId, including alternate product, queue, recipient, or isolated-subaccount conditions.
- Invariant to test: Each signed endpoint action must execute exactly once for the exact intended transaction type, nonce, amount, recipient, and market context.
- Expected HackenProof impact: Critical/High: transaction manipulation that executes a different state change than the user signed.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

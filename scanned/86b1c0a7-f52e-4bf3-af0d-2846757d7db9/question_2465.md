# Q2465: Stale or double-applied slowModeFees

## Question
Can attacker-controlled sequencing make core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner) consume stale slowModeFees or apply the same slowModeFees transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/EndpointTx.sol / validateSignature(bytes32 sender, bytes32 digest, bytes memory signature, bool allowLinkedSigner)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale slowModeFees before all related state is finalized.
- Invariant to test: Each signed endpoint action must execute exactly once for the exact intended transaction type, nonce, amount, recipient, and market context.
- Expected HackenProof impact: Critical/High: transaction manipulation that executes a different state change than the user signed.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

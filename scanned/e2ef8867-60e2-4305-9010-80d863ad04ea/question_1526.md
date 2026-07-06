# Q1526: Ordering dependency around nSubmissions

## Question
Can an attacker manipulate reachable call order so that core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction) observes nSubmissions in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction)
- Entrypoint: User signs an exchange action that the sequencer batches into EndpointTx.processTransactionImpl(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Reorder the same user actions around nSubmissions, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Each signed endpoint action must execute exactly once for the exact intended transaction type, nonce, amount, recipient, and market context.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

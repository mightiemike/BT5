# Q1228: Sender alias or linked-signer confusion

## Question
Can core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) treat msg.sender, signed sender, linked signer, fee owner, builder owner, or recipient-derived address as interchangeable in a way that lets one user spend or settle on behalf of another without fresh authorization?

## Target
- File/function: core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction)
- Entrypoint: User submits a slow-mode transaction through Endpoint.submitSlowModeTransaction(...), then later executes or waits for queue consumption.
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Hold the signed fields constant while varying caller, linked signer, isolated-subaccount mapping, builder ownership, and recipient-derived address interpretation to see whether core/contracts/EndpointTx.sol / processTransactionImpl(bytes calldata transaction) conflates those identities.
- Invariant to test: Only the authorized account or linked signer may execute a state-changing endpoint transaction for that subaccount.
- Expected HackenProof impact: Critical/High: unauthorized transaction or transaction manipulation that mutates the wrong account context.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

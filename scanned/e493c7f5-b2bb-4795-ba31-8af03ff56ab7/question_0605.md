# Q605: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/EndpointTx.sol / processSlowModeTransactionImpl(address sender, bytes calldata transaction) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

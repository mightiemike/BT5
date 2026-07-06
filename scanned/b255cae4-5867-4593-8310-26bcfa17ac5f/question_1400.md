# Q1400: Chain, domain, or contract binding gap

## Question
Can authorization accepted by core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction) be replayed across a different chain, proxy implementation, verifying contract, or helper context because the signed domain does not fully match the execution domain?

## Target
- File/function: core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction)
- Entrypoint: User submits a signed endpoint transaction payload that is later processed through Endpoint.submitTransactionsChecked(...).
- Attacker controls: sender, subaccount, linked signer, nonce, transaction type, productId, amount, liquidatee, sendTo, signature
- Exploit idea: Recreate the same signed payload under alternate chainId, proxy, helper, verifying-contract, or domain-separator contexts and check whether core/contracts/EndpointTx.sol / submitSlowModeTransactionImpl(bytes calldata transaction) still accepts it for a different live execution surface.
- Invariant to test: Signed actions must bind the exact live Nado execution domain and must not survive a change in chain, contract, proxy, or helper context.
- Expected HackenProof impact: Critical/High: replay or unauthorized transaction through insufficient domain separation.
- Fast validation: Build a transaction-sequence test that queues, replays, and reorders endpoint payloads across batch and slow-mode paths, then compare nonce and balance invariants.

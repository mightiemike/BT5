# Q1914: Ordering dependency around filledAmounts

## Question
Can an attacker manipulate reachable call order so that core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) observes filledAmounts in the wrong sequence and therefore settles, withdraws, liquidates, or credits value under assumptions that were only valid before reordering?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Reorder the same user actions around filledAmounts, including queue execution, order matching, funding updates, settlement loops, and withdrawal idx progression, then compare final balances.
- Invariant to test: Filled amount tracking, isolated-subaccount routing, fee accounting, and quote/base deltas must remain conserved across every fill and close path.
- Expected HackenProof impact: Critical/High: reordering or transaction manipulation causing invalid execution or fund loss.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

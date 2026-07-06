# Q1968: Pre-check versus post-effect mismatch

## Question
Can core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner) satisfy an authorization, health, limit, or utilization check before a later effect changes the underlying balance or risk inputs, leaving the final state outside the condition that was actually checked?

## Target
- File/function: core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Locate every require/assert-style gate around core/contracts/OffchainExchange.sol / createIsolatedSubaccount(IEndpoint.CreateIsolatedSubaccount memory txn, address linkedSigner), then mutate the referenced balances, fees, or risk variables later in the same path and compare the checked pre-state to the committed post-state.
- Invariant to test: Safety checks must guard the final committed effect, not only an earlier intermediate state that becomes invalid before the transaction ends.
- Expected HackenProof impact: Critical/High: unauthorized withdrawal, liquidation bypass, or logic attack through check-effect mismatch.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

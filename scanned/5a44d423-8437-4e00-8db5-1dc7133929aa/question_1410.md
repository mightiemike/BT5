# Q1410: Reentrancy or stale-state window at perpEngine.updateBalance(...)

## Question
Can core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId) reach perpEngine.updateBalance(...) before every critical debit, nonce consume, health check, or replay flag is finalized, letting a malicious token or recipient reenter and obtain double-withdrawal, double-credit, or stale-state execution?

## Target
- File/function: core/contracts/OffchainExchange.sol / claimBuilderFee(bytes32 sender, uint32 builderId)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Use a callback-capable token or recipient around perpEngine.updateBalance(...); assert whether balances, marked flags, nonces, or filled amounts are committed before the external interaction.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: reentrancy causing repeated transfer, repeated credit, or stale-state settlement.
- Fast validation: Create parent and isolated subaccounts, then fuzz open/close flows and margin values to assert quote and position conservation across the parent-child boundary.

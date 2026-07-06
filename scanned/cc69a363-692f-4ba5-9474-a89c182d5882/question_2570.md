# Q2570: Arithmetic edge case in feeRate

## Question
Can attacker-controlled extremes of feeRate drive core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Fuzz feeRate around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) mutates balances and risk state.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Create parent and isolated subaccounts, then fuzz open/close flows and margin values to assert quote and position conservation across the parent-child boundary.

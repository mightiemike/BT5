# Q3342: Arithmetic edge case in amount

## Question
Can attacker-controlled extremes of amount drive core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Fuzz amount around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) mutates balances and risk state.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Create parent and isolated subaccounts, then fuzz open/close flows and margin values to assert quote and position conservation across the parent-child boundary.

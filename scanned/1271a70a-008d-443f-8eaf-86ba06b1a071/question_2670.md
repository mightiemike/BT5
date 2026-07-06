# Q2670: Side, price, or amount mutation within matching semantics

## Question
Can a reachable order path through core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) flip side semantics, cross with the wrong maker price, or clip amount incorrectly after size-increment rounding, causing value transfer beyond what either party signed?

## Target
- File/function: core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Test maker/taker role reversal, negative/positive amount flips, reduce-only clipping, and price/amount boundaries that survive crossing checks in core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order).
- Invariant to test: Orders must only match on the intended side, market, size, and maker execution price, with no extra quantity or sign flip.
- Expected HackenProof impact: Critical/High: transaction manipulation or loss of funds through wrong order execution.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

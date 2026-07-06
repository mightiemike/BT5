# Q2642: Replay or cross-context reuse of productId

## Question
Can a signature or signed payload accepted by core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) be replayed in a different context where productId changes meaning, allowing the attacker to reuse valid authorization for a different economic effect?

## Target
- File/function: core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Try to replay the same signed bytes after mutating only the execution context for productId, including alternate product, queue, recipient, or isolated-subaccount conditions.
- Invariant to test: Filled amount tracking, isolated-subaccount routing, fee accounting, and quote/base deltas must remain conserved across every fill and close path.
- Expected HackenProof impact: Critical/High: stealing or loss of funds through bad fill accounting, builder-fee routing, or isolated margin handling.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

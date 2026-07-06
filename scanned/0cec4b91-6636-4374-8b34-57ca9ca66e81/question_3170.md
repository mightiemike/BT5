# Q3170: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/OffchainExchange.sol / updateBuilder(bytes calldata transaction) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateBuilder(bytes calldata transaction)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/OffchainExchange.sol / updateBuilder(bytes calldata transaction) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

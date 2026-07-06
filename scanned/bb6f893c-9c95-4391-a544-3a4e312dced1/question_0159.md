# Q159: Callback-driven post-state ordering bug

## Question
Can a token, recipient, or helper callback interacting around core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) observe a half-updated state and force a second reachable action before all balances, replay markers, fee state, or claim state are finalized?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Use malicious token hooks, recipient fallback logic, helper contracts, or chained calls around core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker); then verify whether any second action can read or exploit intermediate state before finalization.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: reentrancy or transaction manipulation through externally observable intermediate state.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

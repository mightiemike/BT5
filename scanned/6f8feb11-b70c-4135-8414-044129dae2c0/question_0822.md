# Q822: Side, price, or amount mutation within matching semantics

## Question
Can a reachable order path through core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) flip side semantics, cross with the wrong maker price, or clip amount incorrectly after size-increment rounding, causing value transfer beyond what either party signed?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Test maker/taker role reversal, negative/positive amount flips, reduce-only clipping, and price/amount boundaries that survive crossing checks in core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker).
- Invariant to test: Orders must only match on the intended side, market, size, and maker execution price, with no extra quantity or sign flip.
- Expected HackenProof impact: Critical/High: transaction manipulation or loss of funds through wrong order execution.
- Fast validation: Fuzz order digest inputs, filledAmounts tracking, and builder fee fields, then assert the same economic order cannot settle twice or on a different market.

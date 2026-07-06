# Q285: Cross-contract desync of marketInfo

## Question
Can a normal user drive core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) so that marketInfo is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Target the exact moment when core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) mutates marketInfo and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: A user must not create or close isolated subaccounts in a way that steals margin, reuses signatures, or desynchronizes parent-child balances.
- Expected HackenProof impact: Critical/High: logic attack causing incorrect settlement, fee leakage, or cross-account position mutation.
- Fast validation: Create parent and isolated subaccounts, then fuzz open/close flows and margin values to assert quote and position conservation across the parent-child boundary.

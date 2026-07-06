# Q594: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

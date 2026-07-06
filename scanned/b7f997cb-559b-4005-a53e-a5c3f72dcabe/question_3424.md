# Q3424: Rounding leak through minSize

## Question
Can repeated user-controlled updates around minSize make core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving minSize; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

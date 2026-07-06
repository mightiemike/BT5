# Q870: Signature binding gap around priceX18

## Question
Can an unprivileged user reach core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker) through a normal Nado flow where the executed state change depends on priceX18, but the accepted signature or digest path fails to bind priceX18 tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker)
- Entrypoint: User submits an isolated-order payload that EndpointTx routes into OffchainExchange.createIsolatedSubaccount(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Mutate priceX18 after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/OffchainExchange.sol / applyFee(uint32 productId, OrderInfo memory orderInfo, MarketInfo memory market, int128 alreadyMatched, // in quote uint128 appendix, bool taker).
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

# Q3440: Signature binding gap around order.sender

## Question
Can an unprivileged user reach core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize) through a normal Nado flow where the executed state change depends on order.sender, but the accepted signature or digest path fails to bind order.sender tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize)
- Entrypoint: User later closes, claims, or settles through order-driven exchange flows that mutate OffchainExchange state.
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Mutate order.sender after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/OffchainExchange.sol / updateMarket(uint32 productId, uint32 quoteId, int128 sizeIncrement, int128 minSize).
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

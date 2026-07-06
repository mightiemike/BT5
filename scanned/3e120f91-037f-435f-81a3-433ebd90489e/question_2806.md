# Q2806: Hotspot-driven review path

## Question
Does the implementation detail noted for core/contracts/OffchainExchange.sol / matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn) create a reachable exploit path for an unprivileged attacker: filledAmounts is keyed by order digest while isolated routing may rewrite sender after digest lookup.

## Target
- File/function: core/contracts/OffchainExchange.sol / matchOrders(IEndpoint.MatchOrdersWithSigner calldata txn)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Translate the implementation note into an executable proof path and test whether the noted assumption breaks authorization, accounting, queue semantics, or settlement safety.
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

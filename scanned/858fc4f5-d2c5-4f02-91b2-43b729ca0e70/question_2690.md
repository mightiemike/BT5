# Q2690: Signature binding gap around productId

## Question
Can an unprivileged user reach core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order) through a normal Nado flow where the executed state change depends on productId, but the accepted signature or digest path fails to bind productId tightly enough to prevent a semantically different execution?

## Target
- File/function: core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order)
- Entrypoint: User submits signed maker/taker orders that EndpointTx routes into OffchainExchange.matchOrders(...).
- Attacker controls: productId, quoteId, order.sender, priceX18, amount, expiration, nonce, appendix, signature, linked signer
- Exploit idea: Mutate productId after signing while preserving every other signed component and see whether the same authorization still drives a different state transition through core/contracts/OffchainExchange.sol / getDigest(uint32 productId, IEndpoint.Order memory order).
- Invariant to test: An order must execute only according to the maker or taker intent for the exact market, side, amount, price, expiry, and signer context.
- Expected HackenProof impact: Critical/High: unauthorized order execution or transaction manipulation outside signed order intent.
- Fast validation: Write Hardhat tests that replay, partially fill, cancel, and rematch orders while mutating product, appendix, signer, and isolated-subaccount conditions.

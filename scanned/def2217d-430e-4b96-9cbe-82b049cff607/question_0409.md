# Q409: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/PerpEngine.sol / getPositionPnl(uint32 productId, bytes32 subaccount) read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/PerpEngine.sol / getPositionPnl(uint32 productId, bytes32 subaccount)
- Entrypoint: User reaches PerpEngine through matched orders, liquidation, settlement, or socialization paths routed by EndpointTx and OffchainExchange.
- Attacker controls: productId, subaccount, amountDelta, vQuoteDelta, productIds bitmap, insurance availability
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/PerpEngine.sol / getPositionPnl(uint32 productId, bytes32 subaccount) actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Write a Hardhat model test for open/close/flip/settle/socialize sequences and compare realized and unrealized PnL against a reference implementation.

# Q1431: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/BaseEngine.sol / getCoreRisk(bytes32 subaccount, uint32 productId, IProductEngine.HealthType healthType) actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.

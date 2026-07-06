# Q308: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

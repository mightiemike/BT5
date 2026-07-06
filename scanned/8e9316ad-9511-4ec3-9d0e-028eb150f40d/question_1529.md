# Q1529: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta) read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta, int128 quoteDelta) actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.

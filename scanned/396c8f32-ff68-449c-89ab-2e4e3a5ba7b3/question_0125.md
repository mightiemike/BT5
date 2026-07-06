# Q125: Product, quote, or market ID confusion

## Question
Can attacker-controlled productId, quoteId, spread encoding, or isolated-product metadata make core/contracts/libraries/MathSD21x18.sol / module-level logic read or write balances against one market while validation, pricing, or signatures still refer to another?

## Target
- File/function: core/contracts/libraries/MathSD21x18.sol / module-level logic
- Entrypoint: User reaches this library through production callers in Endpoint, Clearinghouse, engines, OffchainExchange, WithdrawPool, or Airdrop.
- Attacker controls: signed and unsigned numeric edge cases, decimals, amounts, product IDs, subaccount encoding, ERC20 return data
- Exploit idea: Mutate product identifiers, spread encodings, quote mappings, isolated-product fields, and product-registration assumptions one bit at a time while tracing which market state core/contracts/libraries/MathSD21x18.sol / module-level logic actually touches.
- Invariant to test: User-controlled identifiers must resolve to exactly one intended market and must not alias another product’s balances, prices, or risk settings.
- Expected HackenProof impact: Critical/High: transaction manipulation or logic attack that settles the wrong market or moves the wrong asset.
- Fast validation: Build a focused fuzz harness around each helper and its production callers, asserting identical semantics against a simple reference implementation.

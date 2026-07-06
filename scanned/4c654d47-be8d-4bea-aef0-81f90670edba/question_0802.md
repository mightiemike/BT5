# Q802: Rounding leak through priceX18

## Question
Can repeated user-controlled updates around priceX18 make core/contracts/BaseEngine.sol / _calculateProductHealth(uint32 productId, bytes32 subaccount, IProductEngine.HealthType healthType) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/BaseEngine.sol / _calculateProductHealth(uint32 productId, bytes32 subaccount, IProductEngine.HealthType healthType)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving priceX18; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.

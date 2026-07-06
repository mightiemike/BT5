# Q2269: Rounding leak through risk weights

## Question
Can repeated user-controlled updates around risk weights make core/contracts/BaseEngine.sol / updatePrice(uint32 productId, int128 priceX18) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/BaseEngine.sol / updatePrice(uint32 productId, int128 priceX18)
- Entrypoint: User reaches BaseEngine bookkeeping indirectly through any deposit, withdraw, trade, liquidation, or settlement action.
- Attacker controls: productId, subaccount, risk weights, nonZeroBalances bitmap state, amount and quote changes
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving risk weights; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Bitmap iteration, health contribution, and risk-weight application must not skip positions, misprice risk, or let attacker-controlled state hide liabilities.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Build a model test that mutates sparse and dense product bitmaps and asserts BaseEngine health contribution matches explicit per-product summation.

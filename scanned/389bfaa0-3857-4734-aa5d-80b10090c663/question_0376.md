# Q376: Rounding leak through priceX18

## Question
Can repeated user-controlled updates around priceX18 make core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving priceX18; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Perp balance state, funding accrual, and PnL computation must remain internally consistent and conserved through every reachable state transition.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

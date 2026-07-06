# Q1880: Rounding leak through withdrawFeeX18

## Question
Can repeated user-controlled updates around withdrawFeeX18 make core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/SpotEngine.sol / updateQuoteFromInsurance(bytes32 subaccount, int128 insurance)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving withdrawFeeX18; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.

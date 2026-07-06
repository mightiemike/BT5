# Q1231: Rounding leak through amountDelta

## Question
Can repeated user-controlled updates around amountDelta make core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/SpotEngine.sol / updateBalance(uint32 productId, bytes32 subaccount, int128 amountDelta)
- Entrypoint: User reaches SpotEngine through deposit, withdrawal, order matching, quote transfer, NLP, or liquidation flows.
- Attacker controls: productId, subaccount, amountDelta, quoteDelta, oracle time, withdrawFeeX18, token decimals
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving amountDelta; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Spot balances, borrow/deposit multipliers, and utilization checks must conserve value across deposits, withdrawals, fills, NLP, and liquidation.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Write invariants that compare spot balances, actual token custody, and utilization after every reachable deposit/withdraw/fill/NLP transition.

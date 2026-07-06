# Q361: Rounding leak through balanceDelta

## Question
Can repeated user-controlled updates around balanceDelta make core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving balanceDelta; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

# Q837: Rounding leak through feesAmt

## Question
Can repeated user-controlled updates around feesAmt make core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount) round in the attacker’s favor so that quote, collateral, fee, or PnL value leaks out of conservation over multiple reachable transactions?

## Target
- File/function: core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Search for floor, ceil, division, multiplier, and size-increment boundaries involving feesAmt; then repeat small-value cycles until any leaked balance becomes measurable.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that drains value via repeated rounding leakage.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

# Q1309: Stale or double-applied totalBorrowsNormalized

## Question
Can attacker-controlled sequencing make core/contracts/SpotEngineState.sol / updateStates(uint128 dt) consume stale totalBorrowsNormalized or apply the same totalBorrowsNormalized transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/SpotEngineState.sol / updateStates(uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale totalBorrowsNormalized before all related state is finalized.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: insolvency through multiplier drift, sign error, or fee-accounting mismatch.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

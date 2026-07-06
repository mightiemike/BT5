# Q993: Arithmetic edge case in depositRateMultiplierX18

## Question
Can attacker-controlled extremes of depositRateMultiplierX18 drive core/contracts/SpotEngineState.sol / updateStates(uint128 dt) into a signedness, scaling, precision, overflow, or underflow edge case that creates value, suppresses losses, or bypasses a health or fee check?

## Target
- File/function: core/contracts/SpotEngineState.sol / updateStates(uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Fuzz depositRateMultiplierX18 around zero, negative/positive sign flips, INT128 bounds, and decimal-conversion boundaries while tracing how core/contracts/SpotEngineState.sol / updateStates(uint128 dt) mutates balances and risk state.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: overflows or underflows, or logic attack that breaks accounting and can lead to fund loss or insolvency.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

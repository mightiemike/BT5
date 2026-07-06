# Q765: Liability saturation or sign-flip saturation gap

## Question
Can attacker-controlled liabilities around core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount) hit a max, min, abs, or sign-flip boundary where debt stops growing correctly, collateral stops shrinking correctly, or a penalty saturates before the real exposure does?

## Target
- File/function: core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Push liabilities, borrows, negative PnL, spread exposures, and liquidation amounts toward every numeric boundary used around core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount); then compare the realized exposure to the mathematically expected exposure.
- Invariant to test: Debt, liability, and penalty accounting must remain monotonic and must not saturate early in a way that benefits the attacker.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack causing hidden liabilities or under-penalized bad debt.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

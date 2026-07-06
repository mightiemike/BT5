# Q1331: Temporary solvency window across sequential updates

## Question
Can core/contracts/SpotEngineState.sol / updateStates(uint128 dt) apply a sequence of balance, funding, fee, or health updates in an order that lets the attacker briefly appear solvent and extract value before the final liability is applied?

## Target
- File/function: core/contracts/SpotEngineState.sol / updateStates(uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Search for sequences where realized credits are applied before liabilities, funding, borrow costs, or fee debits around core/contracts/SpotEngineState.sol / updateStates(uint128 dt); then attempt withdraw, transfer, or match operations inside that intermediate window.
- Invariant to test: A user must never be able to spend, withdraw, or avoid liquidation using equity that exists only during an intermediate update order.
- Expected HackenProof impact: Critical/High: logic attack causing unauthorized withdrawal, liquidation bypass, or system bad debt.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

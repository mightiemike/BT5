# Q496: Spread or encoded-product aliasing

## Question
Can encoded spread state, composite product IDs, or product-bitmaps around core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) alias to a different exposure than the health, pricing, or liquidation logic assumes, letting the attacker hide or reshape risk?

## Target
- File/function: core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Fuzz every encoded spread leg, bitmap, and product-ID composition that reaches core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt), then compare the exposure seen by matching, health, settlement, and liquidation logic.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: logic attack causing hidden liabilities, wrong liquidation behavior, or unauthorized balance mutation through product aliasing.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

# Q729: Dust-cycle extraction or min-threshold bypass

## Question
Can repeated tiny user-controlled operations through core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount) stay below a per-step threshold, rounding guard, fee floor, or min-size rule while still accumulating a meaningful balance, position, or withdrawal advantage over many iterations?

## Target
- File/function: core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Search for floor divisions, min-size exemptions, fee-on-first-fill logic, or first-deposit thresholds around core/contracts/SpotEngineState.sol / tryUnlockNlpBalance(bytes32 subaccount); then repeat the smallest admissible action until any measurable value leak or rule bypass appears.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: logic attack or transaction manipulation that extracts value by exploiting repeated micro-operations.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

# Q226: Cross-engine conservation break

## Question
Can a reachable path through core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) change spot balances, perp balances, insurance, collected fees, or availableSettle in a way that makes the combined system value drift after a complete trade, withdrawal, settlement, or liquidation cycle?

## Target
- File/function: core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Model the full before/after state across spot, perp, clearinghouse, withdraw pool, builder-fee, and insurance accounting around core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt); then look for any delta that is not explained by an explicit fee or transfer.
- Invariant to test: Combined spot, perp, and clearinghouse accounting must conserve value except for explicit fees and real token movements.
- Expected HackenProof impact: Critical/High: logic attack causing bad debt, insolvency, or hidden value leakage across engines.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

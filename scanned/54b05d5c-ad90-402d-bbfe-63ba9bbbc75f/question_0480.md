# Q480: Signedness or zero-crossing bug in accounting math

## Question
Can attacker-controlled sign changes around core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) cause a zero-crossing, absolute-value, or multiplication path to switch accounting regimes incorrectly and grant a balance, rebate, or risk weight the user should not have?

## Target
- File/function: core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Force transitions across positive, zero, and negative boundaries and compare the post-state to a reference implementation that models the intended sign semantics explicitly.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: overflow/underflow or logic attack that breaks accounting and can be monetized.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

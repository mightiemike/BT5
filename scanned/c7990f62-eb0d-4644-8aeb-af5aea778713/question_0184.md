# Q184: Cross-contract desync of totalBorrowsNormalized

## Question
Can a normal user drive core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) so that totalBorrowsNormalized is updated in one contract or storage area but not the corresponding state in another contract, leaving Nado with a reachable balance, position, or authorization desynchronization?

## Target
- File/function: core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Target the exact moment when core/contracts/SpotEngineState.sol / _updateState(uint32 productId, State memory state, uint128 dt) mutates totalBorrowsNormalized and compare post-state across Endpoint, Clearinghouse, engines, pools, and exchange storage after failure, replay, or partial execution.
- Invariant to test: Normalized spot balances, interest multipliers, and fee accrual must preserve conservation of deposits minus borrows except for explicitly collected fees.
- Expected HackenProof impact: Critical/High: logic attack, overflow/underflow, or rounding path that creates or destroys user value incorrectly.
- Fast validation: Build a stateful fuzz harness that applies random deposits, borrows, interest updates, and zero-crossing balance changes, then assert conservation identities hold.

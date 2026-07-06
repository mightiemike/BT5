# Q1232: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/SpotEngineState.sol / updateStates(uint128 dt) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/SpotEngineState.sol / updateStates(uint128 dt)
- Entrypoint: User reaches SpotEngineState internals through deposit, withdrawal, matching, socialization, and interest-update flows.
- Attacker controls: balanceDelta, productId, dt, interest parameters, utilization ratio, borrow/deposit zero crossing
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/SpotEngineState.sol / updateStates(uint128 dt) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Stress signed/unsigned and near-zero transitions in _updateBalanceNormalized(...) and _updateState(...) and compare against a reference model.

# Q410: Same-block or same-transaction multi-call interference

## Question
Can two attacker-controlled calls that both reach core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) within the same block or bundled transaction interfere with each other so that the second call observes partially updated state, stale checks, or unexpectedly shared replay/accounting state?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Bundle duplicate or adjacent calls into the same block or relayed sequence, then compare the result to isolated execution to see whether core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) leaks value or authorization between the calls.
- Invariant to test: Back-to-back reachable calls must not share intermediate state in a way that enables replay, double-credit, wrong-recipient routing, or stale health assumptions.
- Expected HackenProof impact: Critical/High: transaction manipulation, replay, or logic attack through same-block interference.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.

# Q478: Stale or double-applied availableSettle

## Question
Can attacker-controlled sequencing make core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs) consume stale availableSettle or apply the same availableSettle transition twice, causing unauthorized settlement, replayed withdrawal, or incorrect margin accounting?

## Target
- File/function: core/contracts/PerpEngineState.sol / updateStates(uint128 dt, int128[] calldata avgPriceDiffs)
- Entrypoint: User reaches PerpEngineState calculations through matching, liquidation, settlement, and health-check flows.
- Attacker controls: productId, amount, vQuoteBalance, funding index inputs, priceX18
- Exploit idea: Attempt back-to-back calls, delayed queue execution, repeated fills, or same-block sequences that reuse stale availableSettle before all related state is finalized.
- Invariant to test: Perp balance state, funding accrual, and PnL computation must remain internally consistent and conserved through every reachable state transition.
- Expected HackenProof impact: Critical/High: logic attack or arithmetic bug that inflates equity, suppresses losses, or double-applies funding.
- Fast validation: Fuzz balance states and funding deltas near zero, max leverage, and sign flips while comparing PerpEngineState outputs to a model implementation.
